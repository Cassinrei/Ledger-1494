"""
Ledger 1494 — v2: o ledger que não mente.

Herda da v1: contas com natureza, partidas dobradas (débito +, crédito −),
regra da soma zero, saldo derivado, valores em centavos.

Acrescenta três garantias:

1. IMUTABILIDADE
   - Não existe API para editar ou apagar. O diário é append-only.
   - Lançamentos e partidas são objetos congelados (frozen + tuplas).
   - Cada lançamento carrega o hash do anterior (cadeia de hashes):
     qualquer adulteração do histórico é detectada por `verify()`.

2. IDEMPOTÊNCIA POR CHAVE DE NEGÓCIO
   - Todo lançamento exige uma `key` (ex.: "NF-123", "pix:E1234...").
   - Mesma key + mesmo conteúdo  -> devolve o lançamento já existente
     (retry seguro, nada é duplicado).
   - Mesma key + conteúdo diferente -> `IdempotencyConflict`
     (o ledger se recusa a "reescrever" um fato já registrado).

3. ESTORNO POR LANÇAMENTO COMPENSATÓRIO
   - Errou? Não se apaga: lança-se o espelho (sinais invertidos),
     apontando para o original. O original permanece no diário.
   - Um lançamento só pode ser estornado uma vez; um estorno não é estornável
     (para refazer, lance um novo fato com nova key).
   - O estorno também é idempotente (key padrão: "estorno:<key original>").
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from types import MappingProxyType
from typing import Iterable, Mapping

GENESIS = "0" * 64


# ---------------------------------------------------------------- dinheiro

def to_cents(value: int | str | Decimal) -> int:
    """int = já em centavos; str/Decimal = em reais ('10.50' -> 1050)."""
    if isinstance(value, bool):
        raise TypeError("valor inválido")
    if isinstance(value, int):
        return value
    d = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int(d * 100)


def fmt(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    c = abs(cents)
    s = f"{c // 100:,}".replace(",", ".")
    return f"{sign}{s},{c % 100:02d}"


# ---------------------------------------------------------------- erros

class LedgerError(Exception):
    pass


class UnbalancedEntry(LedgerError):
    pass


class IdempotencyConflict(LedgerError):
    pass


class ReversalError(LedgerError):
    pass


class TamperedLedger(LedgerError):
    pass


# ---------------------------------------------------------------- contas

class AccountType(Enum):
    ATIVO = "ativo"
    PASSIVO = "passivo"
    PATRIMONIO = "patrimonio"
    RECEITA = "receita"
    DESPESA = "despesa"

    @property
    def natureza_devedora(self) -> bool:
        return self in (AccountType.ATIVO, AccountType.DESPESA)


@dataclass(frozen=True, slots=True)
class Account:
    code: str
    name: str
    type: AccountType


# ---------------------------------------------------------------- lançamentos

@dataclass(frozen=True, slots=True)
class Posting:
    """Partida. amount > 0 = débito; amount < 0 = crédito."""
    account: str
    amount: int

    @property
    def debit(self) -> int:
        return max(self.amount, 0)

    @property
    def credit(self) -> int:
        return max(-self.amount, 0)


def D(account: str, value) -> Posting:
    return Posting(account, to_cents(value))


def C(account: str, value) -> Posting:
    return Posting(account, -to_cents(value))


def _canonical(postings: Iterable[Posting]) -> tuple[tuple[str, int], ...]:
    """Forma canônica das partidas: agrega por conta e ordena.
    [D(a,10), D(a,5), C(b,15)] == [C(b,15), D(a,15)] para fins de idempotência."""
    agg: dict[str, int] = {}
    for p in postings:
        agg[p.account] = agg.get(p.account, 0) + p.amount
    return tuple(sorted((a, v) for a, v in agg.items() if v != 0))


@dataclass(frozen=True, slots=True)
class JournalEntry:
    seq: int
    key: str
    date: date
    description: str
    postings: tuple[Posting, ...]
    reverses: str | None   # key do lançamento estornado, se for estorno
    prev_hash: str
    hash: str

    def fingerprint(self) -> tuple:
        """O que define 'o mesmo fato' para idempotência."""
        return (self.date.isoformat(), self.description,
                _canonical(self.postings), self.reverses)

    @property
    def is_reversal(self) -> bool:
        return self.reverses is not None


def _hash_entry(seq, key, when, description, postings, reverses, prev_hash) -> str:
    payload = json.dumps({
        "seq": seq,
        "key": key,
        "date": when.isoformat(),
        "description": description,
        "postings": [[p.account, p.amount] for p in postings],
        "reverses": reverses,
        "prev": prev_hash,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


# ---------------------------------------------------------------- livro

class Ledger:
    """Livro append-only. Não há métodos de edição ou exclusão — de propósito."""

    __slots__ = ("_accounts", "_journal", "_by_key", "_reversed_by")

    def __init__(self) -> None:
        self._accounts: dict[str, Account] = {}
        self._journal: list[JournalEntry] = []
        self._by_key: dict[str, JournalEntry] = {}
        self._reversed_by: dict[str, str] = {}   # key original -> key do estorno

    # --- plano de contas (também só cresce)
    def open_account(self, code: str, name: str, type: AccountType) -> Account:
        acc = Account(code, name, type)
        existing = self._accounts.get(code)
        if existing is not None:
            if existing == acc:
                return existing  # idempotente
            raise IdempotencyConflict(f"conta {code} já existe com outra definição")
        self._accounts[code] = acc
        return acc

    def account(self, code: str) -> Account:
        try:
            return self._accounts[code]
        except KeyError:
            raise LedgerError(f"conta {code} não existe") from None

    @property
    def accounts(self) -> Mapping[str, Account]:
        return MappingProxyType(self._accounts)

    # --- registro de fatos
    def post(self, key: str, when: date, description: str,
             postings: Iterable[Posting]) -> JournalEntry:
        """Registra um fato. Idempotente pela `key`."""
        return self._append(key, when, description, tuple(postings), reverses=None)

    def reverse(self, original_key: str, when: date, reason: str = "",
                key: str | None = None) -> JournalEntry:
        """Estorno por lançamento compensatório. Idempotente."""
        orig = self.get(original_key)
        if orig.is_reversal:
            raise ReversalError(
                f"'{original_key}' já é um estorno; lance um novo fato em vez de estornar o estorno")
        key = key or f"estorno:{original_key}"

        # retry do mesmo estorno → idempotente (cai no _append e devolve o existente)
        already = self._reversed_by.get(original_key)
        if already is not None and already != key:
            raise ReversalError(f"'{original_key}' já foi estornado por '{already}'")

        mirror = tuple(Posting(p.account, -p.amount) for p in orig.postings)
        desc = f"Estorno de {original_key}" + (f": {reason}" if reason else "")
        return self._append(key, when, desc, mirror, reverses=original_key)

    def _append(self, key, when, description, postings, reverses) -> JournalEntry:
        if not isinstance(key, str) or not key.strip():
            raise LedgerError("chave de negócio (key) obrigatória")
        self._validate(postings)

        existing = self._by_key.get(key)
        if existing is not None:
            candidate = (when.isoformat(), description, _canonical(postings), reverses)
            if existing.fingerprint() == candidate:
                return existing  # mesmo fato, reenviado: não duplica
            raise IdempotencyConflict(
                f"key '{key}' já registrada (#{existing.seq}) com conteúdo diferente")

        seq = len(self._journal) + 1
        prev = self._journal[-1].hash if self._journal else GENESIS
        h = _hash_entry(seq, key, when, description, postings, reverses, prev)
        entry = JournalEntry(seq, key, when, description, postings, reverses, prev, h)

        self._journal.append(entry)
        self._by_key[key] = entry
        if reverses is not None:
            self._reversed_by[reverses] = key
        return entry

    def _validate(self, ps: tuple[Posting, ...]) -> None:
        if len(ps) < 2:
            raise UnbalancedEntry("um lançamento precisa de ao menos 2 partidas")
        for p in ps:
            self.account(p.account)
            if not isinstance(p.amount, int) or isinstance(p.amount, bool):
                raise LedgerError("valores devem ser inteiros em centavos")
            if p.amount == 0:
                raise LedgerError(f"partida de valor zero em {p.account}")
        total = sum(p.amount for p in ps)
        if total != 0:
            raise UnbalancedEntry(f"soma das partidas = {fmt(total)} (débitos ≠ créditos)")

    # --- consulta
    def get(self, key: str) -> JournalEntry:
        try:
            return self._by_key[key]
        except KeyError:
            raise LedgerError(f"lançamento '{key}' não existe") from None

    def is_reversed(self, key: str) -> bool:
        self.get(key)
        return key in self._reversed_by

    def reversal_of(self, key: str) -> JournalEntry | None:
        rk = self._reversed_by.get(key)
        return self._by_key[rk] if rk else None

    @property
    def journal(self) -> tuple[JournalEntry, ...]:
        return tuple(self._journal)

    @property
    def head(self) -> str:
        """Hash do último lançamento: 'impressão digital' do livro inteiro."""
        return self._journal[-1].hash if self._journal else GENESIS

    # --- saldos derivados
    def _postings_for(self, code: str, as_of: date | None):
        for e in self._journal:
            if as_of is None or e.date <= as_of:
                for p in e.postings:
                    if p.account == code:
                        yield e, p

    def raw_balance(self, code: str, as_of: date | None = None) -> int:
        self.account(code)
        return sum(p.amount for _, p in self._postings_for(code, as_of))

    def balance(self, code: str, as_of: date | None = None) -> int:
        raw = self.raw_balance(code, as_of)
        return raw if self.account(code).type.natureza_devedora else -raw

    def ledger_of(self, code: str, as_of: date | None = None):
        acc = self.account(code)
        running, rows = 0, []
        for e, p in self._postings_for(code, as_of):
            running += p.amount if acc.type.natureza_devedora else -p.amount
            rows.append((e.date, e.key, e.description, p.debit, p.credit, running))
        return rows

    def trial_balance(self, as_of: date | None = None):
        rows, td, tc = [], 0, 0
        for acc in self._accounts.values():
            raw = self.raw_balance(acc.code, as_of)
            d, c = (raw, 0) if raw >= 0 else (0, -raw)
            rows.append((acc, d, c))
            td, tc = td + d, tc + c
        return rows, td, tc

    # --- auditoria
    def verify(self) -> None:
        """Recalcula a cadeia de hashes e as invariantes contábeis.
        Levanta TamperedLedger se o histórico foi adulterado."""
        prev = GENESIS
        seen: set[str] = set()
        for i, e in enumerate(self._journal, start=1):
            if e.seq != i:
                raise TamperedLedger(f"sequência quebrada em #{i}")
            if e.prev_hash != prev:
                raise TamperedLedger(f"elo quebrado em #{e.seq} ({e.key})")
            h = _hash_entry(e.seq, e.key, e.date, e.description,
                            e.postings, e.reverses, e.prev_hash)
            if h != e.hash:
                raise TamperedLedger(f"conteúdo adulterado em #{e.seq} ({e.key})")
            if e.key in seen:
                raise TamperedLedger(f"key duplicada: {e.key}")
            if sum(p.amount for p in e.postings) != 0:
                raise TamperedLedger(f"#{e.seq} não soma zero")
            seen.add(e.key)
            prev = e.hash

        by = {t: 0 for t in AccountType}
        for acc in self._accounts.values():
            by[acc.type] += self.balance(acc.code)
        T = AccountType
        if by[T.ATIVO] != by[T.PASSIVO] + by[T.PATRIMONIO] + by[T.RECEITA] - by[T.DESPESA]:
            raise TamperedLedger("equação patrimonial não fecha")

    # --- impressão
    def print_journal(self) -> None:
        for e in self._journal:
            tag = f"  ↩ estorna {e.reverses}" if e.reverses else (
                "  [estornado]" if e.key in self._reversed_by else "")
            print(f"#{e.seq:<3} {e.date}  {e.key:<22} {e.description}{tag}")
            for p in e.postings:
                side = f"D {fmt(p.debit):>12}" if p.amount > 0 else f"{'':>14}C {fmt(p.credit):>12}"
                print(f"        {p.account:<6} {side}")
            print(f"        hash {e.hash[:16]}…  prev {e.prev_hash[:16]}…")

    def print_trial_balance(self, as_of: date | None = None) -> None:
        rows, td, tc = self.trial_balance(as_of)
        print(f"{'Conta':<32}{'Débito':>14}{'Crédito':>14}")
        print("-" * 60)
        for acc, d, c in rows:
            print(f"{acc.code + ' ' + acc.name:<32}{fmt(d) if d else '':>14}{fmt(c) if c else '':>14}")
        print("-" * 60)
        print(f"{'TOTAL':<32}{fmt(td):>14}{fmt(tc):>14}")


# ---------------------------------------------------------------- demo

if __name__ == "__main__":
    A = AccountType
    L = Ledger()
    L.open_account("1.1", "Caixa", A.ATIVO)
    L.open_account("1.2", "Estoque", A.ATIVO)
    L.open_account("2.1", "Fornecedores", A.PASSIVO)
    L.open_account("3.1", "Capital", A.PATRIMONIO)
    L.open_account("4.1", "Vendas", A.RECEITA)
    L.open_account("5.1", "CMV", A.DESPESA)

    L.post("aporte-001", date(1494, 1, 2), "Aporte de capital",
           [D("1.1", "1000"), C("3.1", "1000")])
    L.post("NF-0001", date(1494, 1, 5), "Compra de mercadoria a prazo",
           [D("1.2", "400"), C("2.1", "400")])

    # Retry do mesmo fato (ex.: timeout na rede): não duplica.
    again = L.post("NF-0001", date(1494, 1, 5), "Compra de mercadoria a prazo",
                   [C("2.1", "400"), D("1.2", "400")])
    print(f"Retry de NF-0001 devolveu #{again.seq} (sem duplicar)")

    # Mesma key, conteúdo diferente: recusado.
    try:
        L.post("NF-0001", date(1494, 1, 5), "Compra de mercadoria a prazo",
               [D("1.2", "4000"), C("2.1", "4000")])
    except IdempotencyConflict as err:
        print(f"Recusado: {err}")

    L.post("venda-0001", date(1494, 1, 9), "Venda à vista",
           [D("1.1", "650"), C("4.1", "650"), D("5.1", "300"), C("1.2", "300")])

    # Pagamento com valor errado → estorno compensatório + lançamento correto.
    L.post("pag-0001", date(1494, 1, 10), "Pagamento a fornecedor",
           [D("2.1", "50"), C("1.1", "50")])
    L.reverse("pag-0001", date(1494, 1, 11), "valor incorreto")
    L.reverse("pag-0001", date(1494, 1, 11), "valor incorreto")  # retry: idempotente
    L.post("pag-0002", date(1494, 1, 12), "Pagamento a fornecedor",
           [D("2.1", "400"), C("1.1", "400")])

    print()
    L.print_journal()
    print()
    L.print_trial_balance()
    L.verify()
    print(f"\nLivro íntegro. head = {L.head[:16]}…")

    # Tentativa de "mentir": adulterar um lançamento antigo por baixo dos panos.
    import dataclasses
    forged = dataclasses.replace(L._journal[1], postings=(D("1.2", "40"), C("2.1", "40")))
    L._journal[1] = forged
    try:
        L.verify()
    except TamperedLedger as err:
        print(f"Adulteração detectada: {err}")
