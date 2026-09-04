import csv

from src.logging.csv_logger import VehicleStateCsvLogger
from src.vehicle import VehicleState


def test_csv_logger_creates_session_file(tmp_path):
    data_dir = tmp_path / "data"

    logger = VehicleStateCsvLogger("session-1", data_dir)
    logger.close()

    assert logger.path == data_dir / "vehicle_state_session-1.csv"
    assert logger.path.is_file()


def test_csv_logger_writes_vehicle_state_header(tmp_path):
    logger = VehicleStateCsvLogger("session-1", tmp_path / "data")
    logger.close()

    with logger.path.open(newline="") as session_file:
        rows = list(csv.reader(session_file))

    assert rows == [
        [
            "timestamp",
            "speed_kmh",
            "steering",
            "throttle",
            "brake",
            "lane_id",
            "indicator",
        ]
    ]


def test_csv_logger_writes_one_vehicle_state(tmp_path):
    logger = VehicleStateCsvLogger("session-1", tmp_path / "data")
    logger.write(
        VehicleState(
            timestamp=1.5,
            speed_kmh=80.0,
            steering=-0.1,
            throttle=0.4,
            brake=0.0,
            lane_id=None,
            indicator="left",
        )
    )
    logger.close()

    with logger.path.open(newline="") as session_file:
        rows = list(csv.reader(session_file))

    assert rows[1] == ["1.5", "80.0", "-0.1", "0.4", "0.0", "", "left"]


def test_csv_logger_writes_multiple_vehicle_states(tmp_path):
    logger = VehicleStateCsvLogger("session-1", tmp_path / "data")
    logger.write(
        VehicleState(
            timestamp=1.0,
            speed_kmh=70.0,
            steering=0.0,
            throttle=0.3,
            brake=0.0,
            lane_id=1,
            indicator="off",
        )
    )
    logger.write(
        VehicleState(
            timestamp=2.0,
            speed_kmh=75.0,
            steering=0.1,
            throttle=0.4,
            brake=0.0,
            lane_id=2,
            indicator="right",
        )
    )
    logger.close()

    with logger.path.open(newline="") as session_file:
        rows = list(csv.reader(session_file))

    assert rows[1:] == [
        ["1.0", "70.0", "0.0", "0.3", "0.0", "1", "off"],
        ["2.0", "75.0", "0.1", "0.4", "0.0", "2", "right"],
    ]
