import unittest
from datetime import date

from ledger import (AccountType as A, C, D, Ledger, LedgerError, Posting,
                    UnbalancedEntry, to_cents)

d = date(1494, 1, 1)


def make():
    L = Ledger()
    L.open_account("cx", "Caixa", A.ATIVO)
    L.open_account("cap", "Capital", A.PATRIMONIO)
    L.open_account("rec", "Receita", A.RECEITA)
    L.open_account("desp", "Despesa", A.DESPESA)
    return L


class TestLedger(unittest.TestCase):
    def test_soma_zero_obrigatoria(self):
        L = make()
        with self.assertRaises(UnbalancedEntry):
            L.post(d, "x", [D("cx", "10"), C("cap", "9")])
        self.assertEqual(L.journal, ())

    def test_minimo_duas_partidas(self):
        with self.assertRaises(UnbalancedEntry):
            make().post(d, "x", [Posting("cx", 0)])

    def test_partida_zero_e_conta_inexistente(self):
        L = make()
        with self.assertRaises(LedgerError):
            L.post(d, "x", [Posting("cx", 0), Posting("cap", 0)])
        with self.assertRaises(LedgerError):
            L.post(d, "x", [D("cx", 1), C("nope", 1)])

    def test_saldo_derivado_e_natureza(self):
        L = make()
        L.post(d, "aporte", [D("cx", "100"), C("cap", "100")])
        L.post(d, "venda", [D("cx", "30"), C("rec", "30")])
        L.post(d, "gasto", [D("desp", "20"), C("cx", "20")])
        self.assertEqual(L.balance("cx"), 11000)
        self.assertEqual(L.balance("cap"), 10000)   # credora, positiva
        self.assertEqual(L.raw_balance("cap"), -10000)
        self.assertEqual(L.balance("desp"), 2000)
        L.check_invariants()

    def test_saldo_em_data(self):
        L = make()
        L.post(date(1494, 1, 1), "a", [D("cx", 10), C("cap", 10)])
        L.post(date(1494, 2, 1), "b", [D("cx", 5), C("cap", 5)])
        self.assertEqual(L.balance("cx", as_of=date(1494, 1, 31)), 10)
        self.assertEqual(L.balance("cx"), 15)

    def test_estorno(self):
        L = make()
        e = L.post(d, "a", [D("cx", 10), C("cap", 10)])
        L.reverse(e.id, d)
        self.assertEqual(L.balance("cx"), 0)
        self.assertEqual(len(L.journal), 2)  # original preservado
        with self.assertRaises(LedgerError):
            L.reverse(e.id, d)

    def test_balancete_fecha(self):
        L = make()
        L.post(d, "a", [D("cx", 700), C("cap", 500), C("rec", 200)])
        _, td, tc = L.trial_balance()
        self.assertEqual(td, tc)

    def test_dinheiro(self):
        self.assertEqual(to_cents("0.1"), 10)
        self.assertEqual(to_cents("10.005"), 1001)
        self.assertEqual(to_cents(5), 5)

    def test_imutabilidade(self):
        L = make()
        e = L.post(d, "a", [D("cx", 1), C("cap", 1)])
        with self.assertRaises(Exception):
            e.description = "mudou"


if __name__ == "__main__":
    unittest.main()
