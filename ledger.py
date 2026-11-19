"""
Ledger de partidas dobradas — o modelo de Pacioli (1494), em memória.

Princípios:
  1. Contas não guardam saldo. Saldo é DERIVADO dos lançamentos.
  2. Todo lançamento tem ≥ 2 partidas e a soma das partidas é ZERO
     (débito = +, crédito = −). Débitos == Créditos, sempre.
  3. O livro é append-only: nada é editado ou apagado; corrige-se com estorno.
  4. Valores em centavos (int) — nunca float.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from itertools import count
from typing import Iterable


# ---------------------------------------------------------------- dinheiro

def to_cents(value: int | str | Decimal) -> int:
    """'10.50' -> 1050. Aceita int (já em centavos), str ou Decimal (em reais)."""
    if isinstance(value, int):
        return value
    d = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int(d * 100)


def fmt(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    c = abs(cents)
    return f"{sign}{c // 100:,}.{c % 100:02d}".replace(",", "X").replace(".", ",").replace("X", ".")


# ---------------------------------------------------------------- contas

class AccountType(Enum):
    ATIVO = "ativo"
    PASSIVO = "passivo"
    PATRIMONIO = "patrimonio"
    RECEITA = "receita"
    DESPESA = "despesa"

    @property
    def natureza_devedora(self) -> bool:
        """Ativo e Despesa crescem a débito; os demais, a crédito."""
        return self in (AccountType.ATIVO, AccountType.DESPESA)


@dataclass(frozen=True)
class Account:
    code: str
    name: str
    type: AccountType


# ---------------------------------------------------------------- lançamentos

@dataclass(frozen=True)
class Posting:
    """Uma partida. amount > 0 = débito; amount < 0 = crédito."""
    account: str
    amount: int

    @property
    def debit(self) -> int:
        return self.amount if self.amount > 0 else 0

    @property
    def credit(self) -> int:
        return -self.amount if self.amount < 0 else 0


def D(account: str, value) -> Posting:
    return Posting(account, to_cents(value))


def C(account: str, value) -> Posting:
    return Posting(account, -to_cents(value))


@dataclass(frozen=True)
class JournalEntry:
    id: int
    date: date
    description: str
    postings: tuple[Posting, ...]
    reverses: int | None = None


class LedgerError(Exception):
    pass


class UnbalancedEntry(LedgerError):
    pass


# ---------------------------------------------------------------- livro

@dataclass
class Ledger:
    _accounts: dict[str, Account] = field(default_factory=dict)
    _journal: list[JournalEntry] = field(default_factory=list)
    _ids: count = field(default_factory=lambda: count(1))

    # --- plano de contas
    def open_account(self, code: str, name: str, type: AccountType) -> Account:
        if code in self._accounts:
            raise LedgerError(f"conta {code} já existe")
        acc = Account(code, name, type)
        self._accounts[code] = acc
        return acc

    def account(self, code: str) -> Account:
        try:
            return self._accounts[code]
        except KeyError:
            raise LedgerError(f"conta {code} não existe") from None

    @property
    def accounts(self) -> list[Account]:
        return list(self._accounts.values())

    # --- diário
    def post(self, when: date, description: str, postings: Iterable[Posting],
             *, reverses: int | None = None) -> JournalEntry:
        ps = tuple(postings)
        self._validate(ps)
        entry = JournalEntry(next(self._ids), when, description, ps, reverses)
        self._journal.append(entry)
        return entry

    def _validate(self, ps: tuple[Posting, ...]) -> None:
        if len(ps) < 2:
            raise UnbalancedEntry("um lançamento precisa de ao menos 2 partidas")
        for p in ps:
            self.account(p.account)  # existe?
            if not isinstance(p.amount, int):
                raise LedgerError("valores devem ser inteiros em centavos")
            if p.amount == 0:
                raise LedgerError(f"partida de valor zero em {p.account}")
        total = sum(p.amount for p in ps)
        if total != 0:
            raise UnbalancedEntry(
                f"soma das partidas = {fmt(total)} (débitos ≠ créditos)")

    def reverse(self, entry_id: int, when: date, reason: str = "") -> JournalEntry:
        """Estorno: novo lançamento com sinais invertidos. O original fica."""
        orig = self.entry(entry_id)
        if any(e.reverses == entry_id for e in self._journal):
            raise LedgerError(f"lançamento {entry_id} já foi estornado")
        flipped = [Posting(p.account, -p.amount) for p in orig.postings]
        desc = f"Estorno de #{entry_id}" + (f": {reason}" if reason else "")
        return self.post(when, desc, flipped, reverses=entry_id)

    def entry(self, entry_id: int) -> JournalEntry:
        for e in self._journal:
            if e.id == entry_id:
                return e
        raise LedgerError(f"lançamento {entry_id} não existe")

    @property
    def journal(self) -> tuple[JournalEntry, ...]:
        return tuple(self._journal)

    # --- razão / saldos derivados
    def _postings_for(self, code: str, as_of: date | None):
        for e in self._journal:
            if as_of is None or e.date <= as_of:
                for p in e.postings:
                    if p.account == code:
                        yield e, p

    def raw_balance(self, code: str, as_of: date | None = None) -> int:
        """Débitos − Créditos (sinal contábil puro)."""
        self.account(code)
        return sum(p.amount for _, p in self._postings_for(code, as_of))

    def balance(self, code: str, as_of: date | None = None) -> int:
        """Saldo na natureza da conta (positivo = saldo 'normal')."""
        raw = self.raw_balance(code, as_of)
        return raw if self.account(code).type.natureza_devedora else -raw

    def ledger_of(self, code: str, as_of: date | None = None):
        """Razão da conta: (data, id, histórico, débito, crédito, saldo corrido)."""
        acc = self.account(code)
        running = 0
        rows = []
        for e, p in self._postings_for(code, as_of):
            running += p.amount if acc.type.natureza_devedora else -p.amount
            rows.append((e.date, e.id, e.description, p.debit, p.credit, running))
        return rows

    def trial_balance(self, as_of: date | None = None):
        """Balancete: (conta, débito, crédito) + totais. Totais sempre iguais."""
        rows, td, tc = [], 0, 0
        for acc in self._accounts.values():
            raw = self.raw_balance(acc.code, as_of)
            d, c = (raw, 0) if raw >= 0 else (0, -raw)
            rows.append((acc, d, c))
            td += d
            tc += c
        assert td == tc, "invariante violada: balancete não fecha"
        return rows, td, tc

    def check_invariants(self) -> None:
        """Soma de todas as partidas do livro == 0 e A = P + PL + (R − D)."""
        assert sum(p.amount for e in self._journal for p in e.postings) == 0
        by = {t: 0 for t in AccountType}
        for acc in self._accounts.values():
            by[acc.type] += self.balance(acc.code)
        T = AccountType
        assert by[T.ATIVO] == by[T.PASSIVO] + by[T.PATRIMONIO] + by[T.RECEITA] - by[T.DESPESA]

    # --- impressão
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

    L.post(date(1494, 1, 2), "Aporte de capital", [D("1.1", "1000"), C("3.1", "1000")])
    L.post(date(1494, 1, 5), "Compra de mercadoria a prazo", [D("1.2", "400"), C("2.1", "400")])
    L.post(date(1494, 1, 9), "Venda à vista", [
        D("1.1", "650"), C("4.1", "650"),
        D("5.1", "300"), C("1.2", "300"),
    ])
    e = L.post(date(1494, 1, 10), "Pagamento errado", [D("2.1", "50"), C("1.1", "50")])
    L.reverse(e.id, date(1494, 1, 11), "valor incorreto")
    L.post(date(1494, 1, 12), "Pagamento a fornecedor", [D("2.1", "400"), C("1.1", "400")])

    try:
        L.post(date(1494, 1, 13), "Desbalanceado", [D("1.1", "10"), C("4.1", "9.99")])
    except UnbalancedEntry as err:
        print(f"Recusado: {err}\n")

    L.print_trial_balance()
    L.check_invariants()
    print(f"\nSaldo do Caixa: {fmt(L.balance('1.1'))}")
