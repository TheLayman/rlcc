# backend/deps.py
# Singleton instances shared across the app.
# Populated by main.py at startup. Imported by receiver.py and other modules.
from backend.config import Config
from backend.storage import Storage
from backend.assembler import TransactionAssembler
from backend.fraud import FraudEngine
from backend.cv_consumer import CVConsumer
from backend.ws import ConnectionManager

config: Config = None
storage: Storage = None
assembler: TransactionAssembler = None
fraud_engine: FraudEngine = None
cv_consumer: CVConsumer = None
ws_manager: ConnectionManager = None
