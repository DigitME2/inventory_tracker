import click
from flask import current_app, g
from flask.cli import with_appcontext
from werkzeug.security import check_password_hash, generate_password_hash
import os

from sqlalchemy import create_engine, Boolean, Column, Date, DateTime, ForeignKey, Integer, Numeric, Sequence, String, \
	Text, inspect
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from sqlalchemy.sql import func

from dbSchema import Base, User, ProductType, StockItem, Settings, ItemId, Bin


def initApp(app):
	# engine = create_engine('postgresql://server:server@localhost:5432/inventorydb')
	engine = create_engine('sqlite:///inventoryDB.sqlite', echo=True)  # temporary for dev use
	Base.metadata.create_all(engine)

	Session = sessionmaker(bind=engine, future=True)
	session = Session()

	# check if the admin user exists. This is used as a proxy for the database being set up
	res = session.query(User).filter(User.username == 'admin').count()
	if res == 0:
		adminUser = User(username='admin', passwordHash=generate_password_hash('admin'), isAdmin=True)
		session.add(adminUser)
		session.add(Settings())  # add a row to settings with the defaults from the DB schema

		# set up placeholder product
		session.add(ProductType(
			id=-1,
			productName="undefined product type",
			tracksSpecificItems=False,
			tracksAllItemsOfProductType=False,
			initialQuantity=0
		))

		# set up an undefined bin location
		session.add(Bin(
			id=-1,
			locationName = "undefined location"
		))

		session.commit()

# app.teardown_appcontext(close_db)


def getDbSession():
	if 'dbSession' not in g:
		# engine = create_engine('postgresql://server:server@localhost:5432/inventorydb')
		engine = create_engine('sqlite:///inventoryDB.sqlite', echo=True)  # temporary for dev use
		Session = sessionmaker(bind=engine)
		g.dbSession = Session()

	return g.dbSession


# TODO: test that this works
def close_db(e=None):
	db = g.pop('dbSession', None)

	if db is not None:
		db.close()
