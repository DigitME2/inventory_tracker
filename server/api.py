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
import logging

from flask import (
	Blueprint, render_template, request, make_response, jsonify
)
from werkzeug.utils import secure_filename

from stockManagement import updateNewStockWithNewProduct
from .auth import login_required
from dbSchema import ProductType, StockItem, Bin, ItemId, CheckInRecord, VerificationRecord, IdAlias, CheckOutRecord
from db import getDbSession
from sqlalchemy import select, or_, create_engine, func
import decimal

bp = Blueprint('api', __name__)



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
				productDict["isAssignedId"] = True
				productDict["assocaitedStockId"] = associatedStockItem.id
		productList.append(productDict)

	return make_response(jsonify(productList), 200)


@bp.route("/getAppBinData")
def getAppBinData():
	dbSession = getDbSession()
	binList = [{"idString": bin_.idString, "locationName": bin_.locationName} for bin_ in dbSession.query(Bin).all()]
	return make_response(jsonify(binList), 200)


# note this function was converted from the rabbitmq worker that was used originally
@bp.route("/addStockRequest", methods=("POST",))
def processAddStockRequest():
	logging.info("Processing request to add item")
	# check barcode number
	# create stock item
	dbSession = getDbSession()
	requestParams = request.json

	if 'requestId' not in requestParams:
		logging.error("requestId not provided")
		return make_response("requestId not provided", 400)

	if 'idString' not in requestParams:
		logging.error("Failed to process request to add item. ID number not provided")
		return make_response(requestParams['requestId'], 400)

	# check the request ID to see if this request has been processed once already. If it has, send a nice message to
	# inform the app and then finish.
	existingCheckinRecord = dbSession.query(CheckInRecord).filter(
		CheckInRecord.createdByRequestId == requestParams['requestId']).first()
	existingCheckoutRecord = dbSession.query(CheckOutRecord).filter(
		CheckOutRecord.createdByRequestId == requestParams['requestId']).first()

	if existingCheckinRecord is not None or existingCheckoutRecord is not None:
		logging.info(f"Skipping duplicate request {requestParams['requestId']}")
		return make_response(requestParams['requestId'], 200)

	logging.info(f"Adding item with ID number {requestParams['idString']}")

	# check if the ID is already in use. If it is, check if it is a particular (non-bulk) item. If so, error out.
	# Also see if the barcode corresponds to a bulk stock item. If it does, create an alias record for the new ID to the
	# existing stock item ID, and then continue
	stockItem = dbSession.query(StockItem).filter(StockItem.idString == requestParams['idString']).first()
	productType = dbSession.query(ProductType).filter(ProductType.barcode == requestParams['barcode']).first()
	checkInRecord = CheckInRecord()

	if stockItem is None:
		# barcode might correspond to an existing bulk stock item. If so, fetch this item, add the new stock,
		# and make an alias record
		if productType is not None and productType.tracksAllItemsOfProductType:
			stockItem = dbSession.query(StockItem).filter(StockItem.productType == productType.id).first()

			aliasRecord = IdAlias(idString=requestParams['idString'], stockItemAliased=stockItem.id)
			dbSession.add(aliasRecord)

			# update itemId table so that the new ID won't get reused elsewhere
			itemId = dbSession.query(ItemId).filter(ItemId.idNumber == requestParams['idString']).first()
			itemId.isPendingAssignment = False
			itemId.isAssigned = True

	# if we not have a stockItem record, it should be a bulk entry, so update it.
	if stockItem is not None:
		if productType.tracksSpecificItems or not productType.tracksAllItemsOfProductType:
			logging.error("Got an ID number that exists for a non-bulk product")
			return make_response(requestParams['requestId'], 400)

		# stockItem is an existing bulk stock entry with a matching ID
		bulkItemCount = 1
		if 'bulkItemCount' in requestParams:
			bulkItemCount = int(requestParams['bulkItemCount'])
		stockItem.quantityRemaining += productType.initialQuantity * bulkItemCount
		checkInRecord.quantityCheckedIn = productType.initialQuantity * bulkItemCount
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
					return make_response(requestParams['requestId'], 400)

			stockItem.price = productType.expectedPrice

	dbSession.flush()

	# this section is common to all request types
	checkInRecord.stockItem = stockItem.id
	checkInRecord.checkInTimestamp = func.now()
	checkInRecord.quantityCheckedIn = productType.initialQuantity
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

	return make_response(requestParams['requestId'], 200)


# note, this function was adapted from the original worker function
@bp.route("/checkStockIn", methods=("POST",))
def processCheckStockInRequest():

	dbSession = getDbSession()
	requestParams = request.json

	if 'requestId' not in requestParams:
		logging.error("requestId not provided")
		return make_response("requestId not provided", 400)

	if 'idString' not in requestParams:
		logging.error("Failed to process request to check in item. ID number not provided")
		return make_response(requestParams['requestId'], 400)

	# check the request ID to see if this request has been processed once already. If it has, send a nice message to
	# inform the app and then finish.
	existingCheckinRecord = dbSession.query(CheckInRecord).filter(
		CheckInRecord.createdByRequestId == requestParams['requestId']).first()
	existingCheckoutRecord = dbSession.query(CheckOutRecord).filter(
		CheckOutRecord.createdByRequestId == requestParams['requestId']).first()

	if existingCheckinRecord is not None or existingCheckoutRecord is not None:
		logging.info(f"Skipping duplicate request {requestParams['requestId']}")
		return make_response(requestParams['requestId'], 200)

	logging.info(f"Processing check in request for stock item with ID number {requestParams['idString']}")

	# attempt to get stockItem. The provided ID might be an alias, so if stockItem is initially null, check for an alias
	stockItem = dbSession.query(StockItem) \
		.filter(StockItem.idNumber == int(requestParams['idString']))\
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
		return make_response(requestParams['requestId'], 400)

	if "quantityCheckingIn" not in requestParams:
		logging.error("Failed to check in stock item. No quantity provided")
		return make_response(requestParams['requestId'], 400)

	checkInRecord = CheckInRecord()
	dbSession.add(checkInRecord)
	checkInRecord.quantityCheckedIn = decimal.Decimal(requestParams['quantityCheckingIn'])
	checkInRecord.checkInTimestamp = func.now()
	checkInRecord.stockItem = stockItem.id

	if "jobId" in requestParams:
		checkInRecord.jobId = requestParams['jobId']

	if 'binId' in requestParams:
		checkInRecord.binId = requestParams['binId']

	stockItem.quantityRemaining += decimal.Decimal(requestParams['quantityCheckingIn'])
	stockItem.isCheckedIn = True
	dbSession.commit()
	return make_response(requestParams['requestId'], 200)


# note, this function was adapted from the original worker function
@bp.route("/checkStockOut", methods=("POST",))
def processCheckStockOutRequest():

	dbSession = getDbSession()
	requestParams = request.json

	if 'requestId' not in requestParams:
		logging.error("requestId not provided")
		return make_response(requestParams['requestId'], 400)

	if 'idString' not in requestParams:
		logging.error("Failed to process request to check out item. ID number not provided")
		return make_response(requestParams['requestId'], 400)

	# check the request ID to see if this request has been processed once already. If it has, send a nice message to
	# inform the app and then finish.
	existingCheckinRecord = dbSession.query(CheckInRecord).filter(
		CheckInRecord.createdByRequestId == requestParams['requestId']).first()
	existingCheckoutRecord = dbSession.query(CheckOutRecord).filter(
		CheckOutRecord.createdByRequestId == requestParams['requestId']).first()

	if existingCheckinRecord is not None or existingCheckoutRecord is not None:
		logging.info(f"Skipping duplicate request {requestParams['requestId']}")
		return make_response(requestParams['requestId'], 200)

	logging.info(f"Processing check out request for stock item with ID number {requestParams['stockIdNumber']}")

	stockItem = dbSession.query(StockItem)\
		.filter(StockItem.idNumber == int(requestParams['stockIdNumber']))\
		.limit(1)\
		.first()

	if stockItem is None:
		logging.error(f"Stock Item {requestParams['stockIdNumber']} does not exist in the database")
		return make_response(requestParams['requestId'], 400)

	isSpecificItem = dbSession.query(ProductType.tracksSpecificItems)\
		.filter(ProductType.id == stockItem.productType).first()[0]
	if stockItem.isCheckedIn is False and isSpecificItem:
		logging.error("Attempting to check out specific item that is already checked out")
		return make_response(requestParams['requestId'], 400)

	checkOutRecord = CheckOutRecord()
	dbSession.add(checkOutRecord)
	checkOutRecord.stockItem = stockItem.id
	checkOutRecord.checkOutTimestamp = func.now()
	checkOutRecord.qtyBeforeCheckout = stockItem.quantityRemaining
	checkOutRecord.createdByRequestId = requestParams['requestId']

	if 'quantityCheckedOut' in requestParams:
		checkOutRecord.quantityCheckedOut = decimal.Decimal(requestParams['quantityCheckedOut'])
	else:
		if dbSession.query(ProductType.tracksSpecificItems).filter(ProductType.id == stockItem.productType).first()[0] is False:
			logging.warning(
				"Bulk or non-specific stock item checked out without specifying quantity. Assuming all."
			)
		checkOutRecord.quantityCheckedOut = stockItem.quantityRemaining  # assumed to be specific items

	stockItem.quantityRemaining -= checkOutRecord.quantityCheckedOut

	if "jobId" in requestParams:
		checkOutRecord.jobId = requestParams['jobId']
	else:
		checkOutRecord.jobId = None

	if isSpecificItem:
		stockItem.isCheckedIn = False

	dbSession.commit()
	return make_response(requestParams['requestId'], 200)