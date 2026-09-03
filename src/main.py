from src.config import (
    CARLA_HOST,
    CARLA_PORT,
    CARLA_TIMEOUT,
    DATA_DIR,
)


def main():
    print("CARLA Study")
    print(f"CARLA_HOST={CARLA_HOST}")
    print(f"CARLA_PORT={CARLA_PORT}")
    print(f"CARLA_TIMEOUT={CARLA_TIMEOUT}")
    print(f"DATA_DIR={DATA_DIR}")


if __name__ == "__main__":
    main()
