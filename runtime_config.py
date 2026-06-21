"""
runtime_config.py — Динамическое изменение настроек бота без перезапуска.
Переопределения хранятся в JSON-файле и применяются поверх статического config.
"""

from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Optional

import config
from logger_setup import get_logger

log = get_logger("runtime_config")

OVERRIDES_FILE = "config_overrides.json"

# Параметры, которые можно менять на ходу
MUTABLE_KEYS: dict[str, tuple[type, str]] = {
    # имя → (тип, описание)
    "risk_pct": (float, "Риск на сделку (%)"),
    "max_positions": (int, "Макс. одновременных позиций"),
    "max_trades_per_symbol_day": (int, "Макс. сделок/символ/день"),
    "max_trades_total_day": (int, "Макс. сделок/день всего"),
    "daily_loss_limit_pct": (float, "Дневной лимит убытков (%)"),
    "portfolio_drawdown_limit": (float, "Лимит просадки портфеля (%)"),
    "min_adx": (float, "Минимальный ADX для входа"),
    "use_trend_filter": (bool, "Использовать EMA200 фильтр"),
    "active_strategies": (list, "Активные стратегии (momentum, volatility)"),
    "symbols": (list, "Список торгуемых символов"),
    "min_atr_filter": (float, "Мин. ATR фильтр"),
}
_IMMUTABLE = {"api_key", "api_secret", "testnet", "leverage", "margin_mode"}


class RuntimeConfig:
    def __init__(self):
        self._lock = threading.Lock()
        self._overrides: dict[str, Any] = {}
        self._loaded = False
        self.load()

    # ─── Загрузка/сохранение ──────────────────────────────────────────

    def load(self):
        with self._lock:
            path = Path(OVERRIDES_FILE)
            if path.exists():
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    self._overrides = {k: v for k, v in raw.items() if k in MUTABLE_KEYS}
                    log.info(f"Загружено {len(self._overrides)} переопределений из {OVERRIDES_FILE}")
                except (json.JSONDecodeError, OSError) as e:
                    log.warning(f"Ошибка загрузки {OVERRIDES_FILE}: {e}")
                    self._overrides = {}
            self._loaded = True

    def save(self):
        with self._lock:
            path = Path(OVERRIDES_FILE)
            try:
                path.write_text(json.dumps(self._overrides, indent=2, ensure_ascii=False), encoding="utf-8")
                log.info(f"Сохранено {len(self._overrides)} переопределений в {OVERRIDES_FILE}")
            except OSError as e:
                log.error(f"Ошибка сохранения {OVERRIDES_FILE}: {e}")

    # ─── Чтение ───────────────────────────────────────────────────────

    def get(self, key: str, default=None):
        with self._lock:
            if key in self._overrides:
                return self._overrides[key]
        return getattr(config, key.upper(), default)

    def get_int(self, key: str, default: int = 0) -> int:
        return int(self.get(key, default))

    def get_float(self, key: str, default: float = 0.0) -> float:
        return float(self.get(key, default))

    def get_bool(self, key: str, default: bool = False) -> bool:
        v = self.get(key, default)
        if isinstance(v, str):
            return v.lower() in ("true", "1", "yes")
        return bool(v)

    def get_list(self, key: str, default: Optional[list] = None) -> list:
        v = self.get(key, default)
        return list(v) if isinstance(v, (list, tuple)) else (default or [])

    # ─── Установка ────────────────────────────────────────────────────

    def set(self, key: str, value: Any) -> tuple[bool, str]:
        key_lower = key.lower()
        if key_lower in _IMMUTABLE:
            return False, f"'{key}' нельзя менять на ходу (защищённый параметр)"
        if key_lower not in MUTABLE_KEYS:
            return False, f"Неизвестный параметр '{key}'. Допустимые: {', '.join(sorted(MUTABLE_KEYS))}"

        expected_type, desc = MUTABLE_KEYS[key_lower]
        try:
            if expected_type == bool:
                if isinstance(value, str):
                    value = value.lower() in ("true", "1", "yes", "on")
                else:
                    value = bool(value)
            elif expected_type == float:
                value = float(value)
            elif expected_type == int:
                value = int(value)
            elif expected_type == list:
                if isinstance(value, str):
                    value = [v.strip() for v in value.split(",") if v.strip()]
                else:
                    value = list(value)
        except (ValueError, TypeError) as e:
            return False, f"Ошибка преобразования '{value}' в {expected_type.__name__}: {e}"

        with self._lock:
            self._overrides[key_lower] = value
        self.save()
        return True, f"{desc} = {value}"

    def delete(self, key: str) -> tuple[bool, str]:
        key_lower = key.lower()
        with self._lock:
            if key_lower in self._overrides:
                del self._overrides[key_lower]
                self.save()
                return True, f"'{key}' — переопределение удалено, используется значение из config"
        return False, f"'{key}' не имеет активного переопределения"

    # ─── Интроспекция ─────────────────────────────────────────────────

    def get_diff(self) -> list[dict]:
        """Возвращает список переопределений с текущими и базовыми значениями."""
        result = []
        with self._lock:
            for key, (typ, desc) in sorted(MUTABLE_KEYS.items()):
                config_key = key.upper()
                base = getattr(config, config_key, "—")
                current = self._overrides.get(key, base)
                if current != base:
                    result.append({"param": key, "description": desc, "base": base, "current": current})
        return result

    def get_all(self) -> dict[str, Any]:
        """Возвращает полную таблицу (базовые + переопределения)."""
        result = {}
        with self._lock:
            for key, (typ, desc) in sorted(MUTABLE_KEYS.items()):
                config_key = key.upper()
                base = getattr(config, config_key, "—")
                current = self._overrides.get(key, base)
                result[key] = {"value": current, "base": base, "description": desc,
                               "overridden": current != base}
        return result

    def reset_all(self):
        with self._lock:
            self._overrides.clear()
        self.save()
        log.info("Все переопределения сброшены")


# Глобальный экземпляр
runtime = RuntimeConfig()
