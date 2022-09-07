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

import functools

from flask import (
    Blueprint, flash, g, redirect, render_template, request, session, url_for, request, make_response, jsonify
)
from werkzeug.exceptions import abort
from werkzeug.security import check_password_hash, generate_password_hash
from sqlalchemy import delete, update
from .db import getDbSession, User
from .auth import login_required, admin_access_required, userHasAdminAccess

bp = Blueprint('users', __name__)


@bp.route('/manageUsers')
@admin_access_required
def manageUsers():
    return render_template('manageUsers.html')


@bp.route('/getUsers')
@admin_access_required
def getUsers():
    dbSession = getDbSession()
    userData = dbSession.query(User).order_by(User.username.asc()).filter(User.username == "admin").all()
    userData += dbSession.query(User).order_by(User.username.asc()).filter(User.username != "admin").all()
    userDataList = [{
            "username": user.username,
            "emailAddress": user.emailAddress,
            "accessLevel": user.accessLevel,
            "receiveStockNotifications": user.receiveStockNotifications
        }
        for user in userData]
    return make_response(jsonify(userDataList), 200)


@bp.route("/addUser", methods=("POST",))
@admin_access_required
def addUser():
    dbSession = getDbSession()

    if dbSession.query(User).filter(User.username == request.form['newUsername']).count() > 0:
        return make_response("username already exists", 400)

    newUser = User(
        username=request.form['newUsername'],
        passwordHash=generate_password_hash(request.form['newPassword']),
        accessLevel=request.form["accessLevel"],
        emailAddress=request.form["emailAddress"],
        receiveStockNotifications=request.form["receiveStockNotifications"] == "true"
    )

    dbSession.add(newUser)
    dbSession.commit()

    return make_response("New user added", 200)


@bp.route("/deleteUser", methods=("POST",))
@admin_access_required
def deleteUser():
    dbSession = getDbSession()
    stmt = delete(User).where(User.username == request.form["username"])
    dbSession.execute(stmt)
    dbSession.commit()

    return make_response("User deleted", 200)


@bp.route("/resetPassword", methods=("POST",))
@admin_access_required
def resetPassword():
    dbSession = getDbSession()

    stmt = update(User)\
        .where(User.username == request.form["username"])\
        .values(passwordHash=generate_password_hash("password"))
    dbSession.execute(stmt)
    dbSession.commit()

    return make_response("password reset", 200)


@bp.route("/changePassword", methods=("GET", "POST"))
@admin_access_required
def changePassword():
    if request.method == "GET":
        return render_template("changePassword.html")
    else:
        dbSession = getDbSession()
        currentHashedPassword = dbSession.query(User.passwordHash).filter(User.username == session['username']).scalar()

        if not check_password_hash(currentHashedPassword, request.form["currentPassword"]):
            return make_response("Current password incorrect", 400)

        stmt = update(User)\
            .where(User.username == session['username'])\
            .values(passwordHash=generate_password_hash(request.form["newPassword"]))
        dbSession.execute(stmt)
        dbSession.commit()

        return make_response("password updated", 200)


@bp.route("/updateUser", methods=("POST",))
@admin_access_required
def updateUser():
    dbSession = getDbSession()

    user = dbSession.query(User).filter(User.username == request.json["username"]).first()
    user.accessLevel = request.json["accessLevel"]
    user.receiveStockNotifications = request.json["receiveStockNotifications"]
    user.emailAddress = request.json["emailAddress"]

    dbSession.commit()

    return make_response("changes saved", 200)
