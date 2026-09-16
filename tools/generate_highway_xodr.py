from __future__ import annotations

from pathlib import Path

from src.scenario.highway_geometry import build_xodr


def main() -> None:
    output_path = Path(__file__).parents[1] / "maps" / "highway_loop_3lane.xodr"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(build_xodr(), encoding="utf-8", newline="\n")
    print(output_path)


if __name__ == "__main__":
    main()
