from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    my_team_id: int
    default_league_id: int
    allowed_origins: tuple[str, ...]
    snapshot_bucket: str | None
    git_revision: str | None
    build_time: str | None

    @classmethod
    def from_env(cls) -> "Settings":
        module_path = Path(__file__).resolve()
        default_data_dir = next(
            (parent / "data" for parent in module_path.parents if (parent / "data").is_dir()),
            module_path.parent / "data",
        )
        origins = os.getenv(
            "FPL_ALLOWED_ORIGINS",
            "http://localhost:3000,http://127.0.0.1:3000",
        )
        return cls(
            data_dir=Path(os.getenv("FPL_DATA_DIR", default_data_dir)),
            my_team_id=int(os.getenv("FPL_MY_TEAM_ID", "2797967")),
            default_league_id=int(os.getenv("FPL_DEFAULT_LEAGUE_ID", "58005")),
            allowed_origins=tuple(origin.strip() for origin in origins.split(",") if origin.strip()),
            snapshot_bucket=os.getenv("FPL_SNAPSHOT_BUCKET", "").strip() or None,
            git_revision=(os.getenv("FPL_GIT_SHA") or os.getenv("K_REVISION") or "").strip() or None,
            build_time=os.getenv("FPL_BUILD_TIME", "").strip() or None,
        )


settings = Settings.from_env()
