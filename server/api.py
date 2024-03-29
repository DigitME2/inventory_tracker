'''
Copyright 2022 DigitME2

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
'''
import datetime
import decimal
import logging
import time

import flask
from flask import (
	Blueprint, request, make_response, jsonify
)
from sqlalchemy import func

from db import getDbSession, close_db
from dbSchema import ProductType, StockItem, Bin, ItemId, CheckInRecord, VerificationRecord, IdAlias, CheckOutRecord, \
	Job, User, CheckingReason

bp = Blueprint('api', __name__)


@bp.teardown_request
def afterRequest(self):
	close_db()

# note that these functions doesn't have login requirement at the moment as
# they're used by the app. TODO: secure this

@bp.route("/getAppProductData")
def getAppProductData():
	dbSession = getDbSession()
	products = dbSession.query(ProductType).all()

	productList = []
	for product in products:
		productDict = {
			"id": product.id,
			"barcode": product.barcode,
			"name": product.productName,
			"expires": product.canExpire,
			"isBulk": product.tracksAllItemsOfProductType,
			"isAssignedStockId": False,
			"associatedStockId": None,
			"quantityUnit": product.quantityUnit
		}
		if product.tracksAllItemsOfProductType:
			associatedStockItem = dbSession.query(StockItem).filter(StockItem.productType == product.id).first()
			if associatedStockItem is not None:
				productDict["isAssignedStockId"] = True
				productDict["associatedStockId"] = associatedStockItem.idString
		productList.append(productDict)

	return make_response(jsonify(productList), 200)


@bp.route("/getAppBinData")
def getAppBinData():
	dbSession = getDbSession()
	binList = [{"idString": bin_.idString, "locationName": bin_.locationName} for bin_ in dbSession.query(Bin).all()]
	return make_response(jsonify(binList), 200)


@bp.route("/getAppJobData")
def getAppJobData():
	dbSession = getDbSession()
	jobs = dbSession.query(Job).all()
	jobList = [{"idString": job.idString, "jobName": job.jobName} for job in jobs]
	return make_response(jsonify(jobList), 200)


@bp.route('/getAppCheckingReasons')
def getAppCheckingReasons():
	dbSession = getDbSession()
	reasons = dbSession.query(CheckingReason).order_by(CheckingReason.reason.asc()).all()
	reasonList = [reason.toDict() for reason in reasons]
	return make_response(jsonify(reasonList), 200)


# fetch a list of itemIds against product barcodes.
@bp.route("/getAppItemIdBarcodeList")
def getAppStockData():
	dbSession = getDbSession()
	itemIdList = dbSession.query(ItemId).order_by(ItemId.idNumber.asc()).all()

	stockBarcodeList = []
	for itemId in itemIdList:
		itemDict = {"itemId": itemId.idString}
		if itemId.isAssigned:
			stockItem = dbSession.query(StockItem).filter(StockItem.idString == itemId.idString).first()

			if stockItem is None: # might be aliased
				alias = dbSession.query(IdAlias).filter(IdAlias.idString == itemId.idString).first()
				if alias is not None:
					stockItem = dbSession.query(StockItem).filter(StockItem.id == alias.stockItemAliased).first()

			if stockItem is not None:
				productType = dbSession.query(ProductType).filter(ProductType.id == stockItem.productType).one()
				itemDict['barcode'] = productType.barcode

		stockBarcodeList.append(itemDict)

	return make_response(jsonify(stockBarcodeList), 200)


@bp.route("/getAppUserIdList")
def getAppUserIdList():
	users = getDbSession().query(User).filter(User.username != "admin").all()
	userList = [{"idString": user.idString, "username": user.username} for user in users]
	return make_response(jsonify(userList), 200)


# note this function was converted from the rabbitmq worker that was used originally
@bp.route("/addStockRequest", methods=("POST",))
def processAddStockRequest():
	logging.info("Processing request to add item")
	try:
		# check barcode number
		# create stock item
		dbSession = getDbSession()
		requestParams = request.json

		if 'requestId' not in requestParams:
			logging.error("requestId not provided")
			return make_response("requestId not provided", 400)

		if 'idString' not in requestParams:
			logging.error("Failed to process request to add item. idString not provided")
			return make_response(jsonify(
				{
					"processedId": requestParams['requestId'],
					"itemId": "",
					"operation": "skipped (err. No item ID)"
				}
			), 200)

		# check the request ID to see if this request has been processed once already. If it has, send a nice message to
		# inform the app and then finish.
		existingCheckinRecord = dbSession.query(CheckInRecord).filter(
			CheckInRecord.createdByRequestId == requestParams['requestId']).first()
		existingCheckoutRecord = dbSession.query(CheckOutRecord).filter(
			CheckOutRecord.createdByRequestId == requestParams['requestId']).first()

		if existingCheckinRecord is not None or existingCheckoutRecord is not None:
			logging.info(f"Skipping duplicate request {requestParams['requestId']}")
			return make_response(jsonify(
				{
					"processedId": requestParams['requestId'],
					"itemId": requestParams['idString'],
					"operation": "skipped (dup.)"
				}
			), 200)

		logging.info(f"Adding item with ID number {requestParams['idString']}")

		# check if the ID is already in use. If it is, check if it is a particular (non-bulk) item. If so, error out.
		# Also see if the barcode corresponds to a bulk stock item. If it does, create an alias record for the new ID to the
		# existing stock item ID, and then continue
		stockItem = dbSession.query(StockItem).filter(StockItem.idString == requestParams['idString']).first()
		productType = dbSession.query(ProductType).filter(ProductType.barcode == requestParams['barcode']).first()
		checkInRecord = CheckInRecord()
		checkInRecord.createdByRequestId = requestParams["requestId"]

		if stockItem is None:
			# barcode might correspond to an existing bulk stock item. If so, fetch this item, add the new stock,
			# and make an alias record
			if productType is not None and productType.tracksAllItemsOfProductType:
				stockItem = dbSession.query(StockItem).filter(StockItem.productType == productType.id).first()

				if stockItem is not None:
					# check if the idString has already been used. If it has, error out
					existingAliasRecord = dbSession.query(IdAlias)\
						.filter(IdAlias.idString == requestParams['idString'])\
						.first()
					if existingAliasRecord is not None:
						logging.error("Got an idString that is already aliased to a stock item")
						return make_response(jsonify(
							{
								"processedId": requestParams['requestId'],
								"itemId": requestParams['idString'],
								"operation": "skipped (ID in use.)"
							}
						), 200)

					aliasRecord = IdAlias(idString=requestParams['idString'], stockItemAliased=stockItem.id)
					dbSession.add(aliasRecord)

					# update itemId table so that the new ID won't get reused elsewhere
					itemId = dbSession.query(ItemId).filter(ItemId.idString == requestParams['idString']).first()
					itemId.isPendingAssignment = False
					itemId.isAssigned = True

		# if we now have a stockItem record, it should be a bulk entry, so update it.
		if stockItem is not None:
			if productType.tracksSpecificItems or not productType.tracksAllItemsOfProductType:
				logging.error("Got an ID number that exists for a non-bulk product")
				return make_response(jsonify(
					{
						"processedId": requestParams['requestId'],
						 "itemId": requestParams['idString'],
						 "operation": "skipped (ID in use.)"
					}
				), 200)

			# stockItem is an existing bulk stock entry with a matching ID
			bulkItemCount = 1
			if 'bulkItemCount' in requestParams:
				bulkItemCount = int(requestParams['bulkItemCount'])
			stockItem.quantityRemaining += productType.initialQuantity * bulkItemCount
			#checkInRecord.quantity = productType.initialQuantity * bulkItemCount
			# continues below ...

		# itemID is not attached to a stock item. Create one.
		else:
			stockItem = StockItem()
			dbSession.add(stockItem)
			itemId = dbSession.query(ItemId).filter(ItemId.idString == requestParams['idString']).first()
			itemId.isPendingAssignment = False
			itemId.isAssigned = True
			stockItem.idString = requestParams['idString']
			stockItem.addedTimestamp = func.now()
			stockItem.isCheckedIn = True

			if 'barcode' not in requestParams \
					or (dbSession.query(ProductType).filter(ProductType.barcode == requestParams['barcode']).count() == 0):
				productType = dbSession \
					.query(ProductType) \
					.filter(ProductType.productName == "undefined product type") \
					.first()
				stockItem.productType = productType.id
				stockItem.quantityRemaining = productType.initialQuantity
				stockItem.price = productType.expectedPrice

				if 'quantityCheckingIn' in requestParams:
					stockItem.quantityRemaining = decimal.Decimal(requestParams['quantityCheckingIn'])
			else:
				stockItem.productType = productType.id
				if 'quantityCheckingIn' in requestParams:
					stockItem.quantityRemaining = decimal.Decimal(requestParams['quantityCheckingIn'])
				elif productType.tracksAllItemsOfProductType and 'bulkItemCount' in requestParams:
					stockItem.quantityRemaining = productType.initialQuantity * int(requestParams['bulkItemCount'])
				else:
					stockItem.quantityRemaining = productType.initialQuantity

				if productType.canExpire:
					if 'expiryDate' in requestParams:
						stockItem.expiryDate = datetime.datetime.strptime(requestParams['expiryDate'], "%Y-%m-%d")
					else:
						dbSession.rollback()
						return make_response(jsonify(
							{
								"processedId": requestParams['requestId'],
								"itemId": requestParams['idString'],
								"operation": "skipped (expiry not set)"
							}
						), 200)

				stockItem.price = productType.expectedPrice

				if "batchNumber" in requestParams:
					stockItem.batchNumber = requestParams['batchNumber']

				if "serialNumber" in requestParams:
					stockItem.serialNumber = requestParams['serialNumber']

				if 'dateOfManufacture' in requestParams:
					stockItem.dateOfManufacture = datetime.datetime.strptime(requestParams['dateOfManufacture'], "%Y-%m-%d")

	except Exception as e:
		logging.error(e)
		# a positive response is returned even in the case of an error, as the app MUST remove the
		# request from its list, otherwise it'll just keep coming back
		return make_response(jsonify({"status": "error"}), 200)


	dbSession.flush()

	# this section is common to all request types
	checkInRecord.stockItem = stockItem.id
	checkInRecord.productType = productType.id
	checkInRecord.timestamp = func.now()
	if checkInRecord.quantity is None:
		checkInRecord.quantity = productType.initialQuantity
	if 'quantityCheckingIn' in requestParams:
		checkInRecord.quantity = decimal.Decimal(requestParams['quantityCheckingIn'])
	elif 'bulkItemCount' in requestParams:
		checkInRecord.quantity = decimal.Decimal(requestParams['bulkItemCount']) * productType.initialQuantity
	checkInRecord.createdByRequestId = requestParams['requestId']

	if 'binIdString' in requestParams:
		checkInRecord.binId = dbSession.query(Bin.id).filter(Bin.idString == requestParams['binIdString']).first()[0]

	dbSession.add(checkInRecord)
	dbSession.flush()
	verificationRecord = VerificationRecord()
	verificationRecord.associatedCheckInRecord = checkInRecord.id
	verificationRecord.associatedStockItemId = stockItem.id
	verificationRecord.isVerified = False
	if "barcode" in requestParams:
		verificationRecord.itemBarcode = requestParams['barcode']
	dbSession.add(verificationRecord)
	dbSession.commit()

	return make_response(jsonify(
		{
			"processedId": requestParams['requestId'],
			"itemId": requestParams['idString'],
			"operation": "added"
		}
	), 200)


# note, this function was adapted from the original worker function
@bp.route("/checkStockIn", methods=("POST",))
def processCheckStockInRequest():
	try:
		dbSession = getDbSession()
		requestParams = request.json

		if 'requestId' not in requestParams:
			logging.error("requestId not provided")
			return make_response("requestId not provided", 200)

		if 'idString' not in requestParams:
			logging.error("Failed to process request to check in item. ID number not provided")
			return make_response(
				jsonify(
					{
						"processedId": requestParams['requestId'],
						"itemId": "",
						"operation": "skipped (err. No item ID)"
					}
				), 200)

		# check the request ID to see if this request has been processed once already. If it has, send a nice message to
		# inform the app and then finish.
		existingCheckinRecord = dbSession.query(CheckInRecord).filter(
			CheckInRecord.createdByRequestId == requestParams['requestId']).first()
		existingCheckoutRecord = dbSession.query(CheckOutRecord).filter(
			CheckOutRecord.createdByRequestId == requestParams['requestId']).first()

		if existingCheckinRecord is not None or existingCheckoutRecord is not None:
			logging.info(f"Skipping duplicate request {requestParams['requestId']}")
			return make_response(jsonify(
				{
					"processedId": requestParams['requestId'],
					"itemId": requestParams['idString'],
					"operation": "skipped (dup.)"
				}
			), 200)

		logging.info(f"Processing check in request for stock item with ID number {requestParams['idString']}")

		# attempt to get stockItem. The provided ID might be an alias, so if stockItem is initially null, check for an alias
		stockItem = dbSession.query(StockItem) \
			.filter(StockItem.idString == requestParams['idString'])\
			.first()

		if stockItem is None:
			alias = dbSession.query(IdAlias).filter(IdAlias.idString == requestParams['idString']).first()
			if alias:
				stockItem = dbSession.query(StockItem) \
					.filter(StockItem.idNumber == alias.stockItemAliased)\
					.first()

		if stockItem is None:
			logging.error(f"Stock Item {requestParams['idString']} does not exist in the database")

		if stockItem.isCheckedIn is True and dbSession.query(ProductType.tracksSpecificItems) \
				.filter(ProductType.id == stockItem.productType).first()[0] is True:
			logging.error("Attempting to check in specific item that is already checked in")
			return make_response(jsonify(
				{
					"processedId": requestParams['requestId'],
					"itemId": requestParams['idString'],
					"operation": "skipped (checked in)",
				}
			), 200)

		if "quantity" not in requestParams:
			logging.error("Failed to check in stock item. quantity must be provided")
			return make_response(jsonify(
				{
					"processedId": requestParams['requestId'],
					"itemId": requestParams["idString"],
					"operation": "error (quantity needed)"
				}
			), 200)

		checkInRecord = CheckInRecord()
		dbSession.add(checkInRecord)
		checkInRecord.quantity = decimal.Decimal(requestParams['quantity'])
		checkInRecord.stockItem = stockItem.id
		checkInRecord.productType = stockItem.productType
		checkInRecord.createdByRequestId = requestParams["requestId"]

		if 'timestamp' in requestParams:
			checkInRecord.timestamp = datetime.datetime.strptime(requestParams['timestamp'], "%Y-%m-%d %H:%M:%S")

		if "jobIdString" in requestParams:
			job = dbSession.query(Job).filter(Job.idString == requestParams['jobIdString']).first()
			job.lastUpdated = func.current_timestamp()
			checkInRecord.jobId = job.id

		if 'binIdString' in requestParams:
			checkInRecord.binId = dbSession.query(Bin.id).filter(Bin.idString == requestParams['binIdString']).first()[0]

		if 'userIdString' in requestParams:
			checkInRecord.userId = \
				dbSession.query(User.id).filter(User.idString == requestParams["userIdString"]).first()[0]

		if "reasonId" in requestParams:
			checkInRecord.reasonId = requestParams["reasonId"]

		stockItem.quantityRemaining += decimal.Decimal(requestParams['quantity'])
		stockItem.isCheckedIn = True
		stockItem.lastUpdated = func.current_timestamp()

		dbSession.commit()

		return make_response(
			jsonify(
				{
					"processedId": requestParams['requestId'],
					"itemId": requestParams['idString'],
					"operation": "checked in"
				}
			), 200)
	except Exception as e:
		logging.error(e)


# note, this function was adapted from the original worker function
@bp.route("/checkStockOut", methods=("POST",))
def processCheckStockOutRequest():
	try:
		dbSession = getDbSession()
		requestParams = request.json

		if 'requestId' not in requestParams:
			logging.error("requestId not provided")
			return make_response("request ID not provided", 200)

		if 'idString' not in requestParams:
			logging.error("Failed to process request to check out item. ID number not provided")
			# note that even though this is an error, the response is a 200 so that the app can still
			# delete the request, otherwise it'll just keep coming back
			return make_response(
				jsonify(
					{
						"processedId": requestParams['requestId'],
						"itemId": "",
						"operation": "skipped (err. No item ID)"
					}
				), 200)

		# check the request ID to see if this request has been processed once already. If it has, send a nice message to
		# inform the app and then finish.
		existingCheckinRecord = dbSession.query(CheckInRecord).filter(
			CheckInRecord.createdByRequestId == requestParams['requestId']).first()
		existingCheckoutRecord = dbSession.query(CheckOutRecord).filter(
			CheckOutRecord.createdByRequestId == requestParams['requestId']).first()

		if existingCheckinRecord is not None or existingCheckoutRecord is not None:
			logging.info(f"Skipping duplicate request {requestParams['requestId']}")
			return make_response(jsonify(
				{
					"processedId": requestParams['requestId'],
					"itemId": requestParams['idString'],
					"operation": "skipped (dup.)"
				}
			), 200)

		logging.info(f"Processing check out request for stock item with ID number {requestParams['idString']}")

		# attempt to get the stock item from the id string
		stockItem = dbSession.query(StockItem)\
			.filter(StockItem.idString == requestParams['idString'])\
			.first()

		# if the stock item wasn't found, check for an alias
		if stockItem is None:
			alias = dbSession.query(IdAlias).filter(IdAlias.idString == requestParams['idString']).first()
			if alias:
				stockItem = dbSession.query(StockItem).filter(StockItem.id == alias.stockItemAliased).first()

		if stockItem is None:
			logging.error(f"Stock Item {requestParams['idString']} does not exist in the database")
			return make_response(jsonify(
				{
					"processedId": requestParams['requestId'],
					"itemId": requestParams["idString"],
					"operation": "skipped (does not exist)"
				}
			), 200)

		isSpecificItem = dbSession.query(ProductType.tracksSpecificItems)\
			.filter(ProductType.id == stockItem.productType).first()[0]
		if stockItem.isCheckedIn is False and isSpecificItem:
			logging.error("Attempting to check out specific item that is already checked out")
			return make_response(jsonify(
				{
					"processedId": requestParams['requestId'],
					"itemId": requestParams["idString"],
					"operation": "skipped (already checked out)"
				}
			), 200)

		checkOutRecord = CheckOutRecord()
		dbSession.add(checkOutRecord)
		checkOutRecord.stockItem = stockItem.id
		checkOutRecord.productType = stockItem.productType
		checkOutRecord.timestamp = func.now()
		checkOutRecord.qtyBeforeCheckout = stockItem.quantityRemaining
		checkOutRecord.createdByRequestId = requestParams['requestId']

		if 'timestamp' in requestParams:
			checkOutRecord.timestamp = datetime.datetime.strptime(requestParams['timestamp'], "%Y-%m-%d %H:%M:%S")

		if "jobIdString" in requestParams:
			job = dbSession.query(Job).filter(Job.idString == requestParams['jobIdString']).first()
			job.lastUpdated = func.current_timestamp()
			checkOutRecord.jobId = job.id

		if 'binIdString' in requestParams:
			checkOutRecord.binId = dbSession.query(Bin.id).filter(Bin.idString == requestParams['binIdString']).first()[0]

		if 'userIdString' in requestParams:
			checkOutRecord.userId = \
				dbSession.query(User.id).filter(User.idString == requestParams["userIdString"]).first()[0]

		if "reasonId" in requestParams:
			checkOutRecord.reasonId = requestParams["reasonId"]

		if 'quantity' in requestParams:
			quantity = decimal.Decimal(requestParams['quantity'])
			if quantity > stockItem.quantityRemaining:
				logging.error(f"Attempting to check out {quantity} of {stockItem.idString}. Quantity available is {stockItem.quantityRemaining}")
				return make_response(jsonify(
					{
						"processedId": requestParams['requestId'],
						"itemId": requestParams["idString"],
						"operation": "skipped (quantity greater than available)"
					}
				), 200)
			checkOutRecord.quantity = quantity
		else:
			if dbSession.query(ProductType.tracksSpecificItems).filter(ProductType.id == stockItem.productType).first()[0] is False:
				logging.info(
					"Bulk or non-specific stock item checked out without specifying quantity. Assuming all."
				)
			checkOutRecord.quantity = stockItem.quantityRemaining  # assumed to be specific items

		stockItem.quantityRemaining -= checkOutRecord.quantity

		if isSpecificItem:
			stockItem.isCheckedIn = False

		dbSession.commit()

		return make_response(
			jsonify(
				{
					"processedId": requestParams['requestId'],
					"itemId": requestParams['idString'],
					"operation": "checked out"
				}
			), 200)
	except Exception as e:
		logging.error(e)
