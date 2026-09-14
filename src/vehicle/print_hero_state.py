from src.vehicle import CarlaVehicleClient


def main() -> None:
    print(CarlaVehicleClient(host="127.0.0.1", port=2000).get_state())


if __name__ == "__main__":
    main()
