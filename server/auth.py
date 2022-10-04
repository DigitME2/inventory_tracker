"""
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
"""

import functools

from flask import (
	Blueprint, flash, g, redirect, render_template, request, session, url_for, request
)
from werkzeug.exceptions import abort
from werkzeug.security import check_password_hash, generate_password_hash
from db import getDbSession, User

bp = Blueprint('auth', __name__)


@bp.route('/login', methods=('GET', 'POST'))
def login():
	if request.method == 'POST':
		username = request.form['username']
		password = request.form['password']
		dbSession = getDbSession()
		error = None
		user = dbSession.query(User).filter(User.username == username).first()
		
		if user is None:
			error = 'Incorrect username'
		elif not check_password_hash(user.passwordHash, password):
			error = 'Incorrect login'
			
		if error is None:
			session.clear()
			session['username'] = user.username
			return redirect(url_for('overview.getOverview'))

		flash(error)

	return render_template('login.html')


@bp.before_app_request
def load_logged_in_user():
	username = session.get('username')

	if username is None:
		g.user = None
	else:
		dbSession = getDbSession()
		g.user = dbSession.query(User).filter(User.username == username).first()
		print(g.user)



		
@bp.route('/logout')
def logout():
	session.clear()
	return redirect(url_for('auth.login'))


def login_required(view):
	@functools.wraps(view)
	def wrapped_view(**kwargs):
		if g.user is None:
			return redirect(url_for('auth.login'))
		
		return view(**kwargs)
	
	return wrapped_view

def admin_access_required(view):
	@functools.wraps(view)
	def wrapped_view(**kwargs):
		if g.user is None:
			return redirect(url_for('auth.login'))
		elif g.user.accessLevel < 3:
			return abort(403)

		return view(**kwargs)

	return wrapped_view

def create_access_required(view):
	@functools.wraps(view)
	def wrapped_view(**kwargs):
		if g.user is None:
			return redirect(url_for('auth.login'))
		elif g.user.accessLevel < 2:
			return abort(403)

		return view(**kwargs)

	return wrapped_view

def edit_access_required(view):
	@functools.wraps(view)
	def wrapped_view(**kwargs):
		if g.user is None:
			return redirect(url_for('auth.login'))
		elif g.user.accessLevel < 1:
			return abort(403)

		return view(**kwargs)

	return wrapped_view


def userHasAdminAccess():
	if g.user is not None and g.user.accessLevel == 3:
		return True
	return False


def userHasCreateAccess():
	if g.user is not None and g.user.accessLevel >= 2:
		return True
	return False


def userHasEditAccess():
	if g.user is not None and g.user.accessLevel >= 1:
		return True
	return False
