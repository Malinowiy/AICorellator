from abc import ABC, abstractmethod

from aicorellator.models import ScanResult


class ScannerModule(ABC):
    """
    Базовый интерфейс модуля сканирования.

    Каждый модуль:
      1. Запускает свой внешний инструмент (subprocess, API, etc.)
      2. Парсит его вывод
      3. Нормализует в ScanResult (Node/Edge/Provenance)
    """

    name: str = "unnamed"
    command: str | None = None
    required: bool = False

    @abstractmethod
    def run(self) -> ScanResult:
        """Запускает инструмент и возвращает нормализованный результат."""
        ...

    def is_available(self) -> bool:
        """Проверка, что инструмент установлен и доступен."""
        return True