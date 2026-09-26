"""Check settings: what's in checks.toml (+ checks.local.toml), with changes
made in the web interface layered on top.

The files stay the defaults and the documentation; a change made in the
browser is stored in the database (check_settings table) and wins over the
file. Setting a value back to what the file says removes the change, and
"reset" removes all changes for a check.

Only settings that exist in the files can be changed (plus `enabled`), and
a new value must have the same type as the file's value, so a typo in the
browser can't produce a configuration the check doesn't understand.
"""
import copy
import logging
from dataclasses import dataclass, field
from typing import Any

from intervals_auditor.checks import CHECKS
from intervals_auditor.db import Database
from intervals_auditor.repositories import CheckSettingRepository
from intervals_auditor.timeutil import to_iso

log = logging.getLogger(__name__)


class CheckSettingsError(ValueError):
    pass


def value_kind(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "json"
    return "json"


def coerce(kind: str, value: Any, *, where: str) -> Any:
    """Check a new value against the type the file uses for it."""
    ok = {
        "bool": lambda v: isinstance(v, bool),
        "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "float": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "str": lambda v: isinstance(v, str),
        "list": lambda v: isinstance(v, list),
        "json": lambda v: isinstance(v, (dict, list)),
    }[kind]
    if not ok(value):
        raise CheckSettingsError(f"{where} must be {'a number' if kind in ('int', 'float') else 'a ' + kind}, "
                                 f"got {value!r}")
    return float(value) if kind == "float" else value


@dataclass
class CheckParam:
    key: str
    value: Any
    file_value: Any
    kind: str
    changed: bool


@dataclass
class CheckSettings:
    name: str
    description: str
    enabled: bool
    enabled_changed: bool
    params: list[CheckParam] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.enabled_changed or any(p.changed for p in self.params)


class CheckSettingsService:
    def __init__(self, db: Database, repo: CheckSettingRepository, file_config: dict, *, clock):
        self.db = db
        self.repo = repo
        self.file_config = file_config
        self.clock = clock

    def _file(self, name: str) -> dict:
        return self.file_config.get(name, {})

    def effective(self) -> dict:
        """The configuration the checks run with."""
        config = copy.deepcopy(self.file_config)
        for name, overrides in self.repo.all().items():
            config.setdefault(name, {}).update(overrides)
        return config

    def describe(self) -> list[CheckSettings]:
        overrides = self.repo.all()
        result = []
        for name, cls in CHECKS.items():
            file_cfg, changed = self._file(name), overrides.get(name, {})
            params = []
            for key, file_value in file_cfg.items():
                if key == "enabled":
                    continue
                params.append(CheckParam(key=key, value=changed.get(key, file_value), file_value=file_value,
                                         kind=value_kind(file_value), changed=key in changed))
            file_enabled = bool(file_cfg.get("enabled", False))
            result.append(CheckSettings(
                name=name, description=cls.description,
                enabled=bool(changed.get("enabled", file_enabled)), enabled_changed="enabled" in changed,
                params=params,
            ))
        return result

    def get(self, name: str) -> CheckSettings:
        for settings in self.describe():
            if settings.name == name:
                return settings
        raise CheckSettingsError(f"Unknown check {name!r}. Available: {', '.join(CHECKS)}")

    def update(self, name: str, values: dict[str, Any]) -> CheckSettings:
        """Change some settings of a check. A value equal to the file's value
        removes the change for that setting."""
        self.get(name)  # validates the name
        file_cfg = self._file(name)
        cleaned = {}
        for key, value in values.items():
            if key == "enabled":
                cleaned[key] = coerce("bool", value, where=f"{name}.enabled")
            elif key in file_cfg:
                cleaned[key] = coerce(value_kind(file_cfg[key]), value, where=f"{name}.{key}")
            else:
                raise CheckSettingsError(f"{name} has no setting {key!r}")
        try:  # the check's own rules, e.g. the shape of a table
            CHECKS[name].validate_config({**self.effective().get(name, {}), **cleaned})
        except ValueError as e:
            raise CheckSettingsError(f"{name}: {e}") from None
        now = to_iso(self.clock())
        with self.db.transaction():
            for key, value in cleaned.items():
                default = bool(file_cfg.get("enabled", False)) if key == "enabled" else file_cfg[key]
                if value == default:
                    self.repo.delete(name, key)
                else:
                    self.repo.set(name, key, value, now)
        log.info("Check settings changed for %s: %s", name, ", ".join(f"{k}={v!r}" for k, v in cleaned.items()))
        return self.get(name)

    def reset(self, name: str) -> CheckSettings:
        self.get(name)
        with self.db.transaction():
            self.repo.delete(name)
        log.info("Check settings for %s reset to the configuration files", name)
        return self.get(name)
