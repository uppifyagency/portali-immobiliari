"""Un modulo per portale: il nome del file è il nome del portale e il modulo espone la classe Client."""
import importlib
import pkgutil
from collections.abc import Iterator, Mapping


class _Registro(Mapping):
    """Nome del portale -> classe Client. Il modulo si importa alla prima richiesta:
    un portale che non si carica ferma solo le ricerche su quel portale."""

    def __init__(self):
        self._nomi = tuple(sorted(m.name for m in pkgutil.iter_modules(__path__)))

    def __getitem__(self, nome: str) -> type:
        if nome not in self._nomi:
            raise KeyError(nome)
        return importlib.import_module(f".{nome}", __name__).Client

    def __contains__(self, nome: object) -> bool:
        return nome in self._nomi

    def __iter__(self) -> Iterator[str]:
        return iter(self._nomi)

    def __len__(self) -> int:
        return len(self._nomi)


PORTALI: Mapping[str, type] = _Registro()
