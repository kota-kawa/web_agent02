from __future__ import annotations

import logging
import os
from pathlib import Path

from browser_use.env_loader import load_secrets_env

load_secrets_env()

logging.basicConfig(level=os.environ.get('FLASK_LOG_LEVEL', 'INFO'))
logger = logging.getLogger('flask_app.app')

APP_STATIC_DIR = Path(__file__).resolve().parent / 'static'
