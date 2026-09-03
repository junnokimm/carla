import os

CARLA_HOST = os.getenv("CARLA_HOST", "127.0.0.1")
CARLA_PORT = int(os.getenv("CARLA_PORT", "2000"))
CARLA_TIMEOUT = float(os.getenv("CARLA_TIMEOUT", "10.0"))
DATA_DIR = os.getenv("DATA_DIR", "data")
