from flask import Blueprint

webarena_bp = Blueprint('webarena', __name__, template_folder='../templates')

from . import routes
