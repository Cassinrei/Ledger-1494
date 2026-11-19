import dataclasses
import unittest
from datetime import date

from ledger import (GENESIS, AccountType as A, C, D, IdempotencyConflict,
                    Ledger, LedgerError, ReversalError, TamperedLedger,
                    UnbalancedEntry)

d1, d2 = date(1494, 1, 1), date(1494, 1, 2)


def make():
    L = Ledger()
    L.open_account("cx", "Caixa", A.ATIVO)
    L.open_account("cap", "Capital", A.PATRIMONIO)
    L.open_account("rec", "Receita", A.RECEITA)
    L.open_account("desp", "Despesa", A.DESPESA)
    return L


class Basico(unittest.TestCase):
    def test_soma_zero(self):
        L = make()
        with self.assertRaises(UnbalancedEntry):
            L.post("k", d1, "x", [D("cx", 10), C("cap", 9)])
        self.assertEqual(L.journal, ())

    def test_key_obrigatoria(self):
        with self.assertRaises(LedgerError):
            make().post("", d1, "x", [D("cx", 1), C("cap", 1)])

    def test_saldo_derivado(self):
        L = make()
        L.post("a", d1, "aporte", [D("cx", "100"), C("cap", "100")])
        L.post("b", d1, "venda", [D("cx", "30"), C("rec", "30")])
        L.post("c", d1, "gasto", [D("desp", "20"), C("cx", "20")])
        self.assertEqual(L.balance("cx"), 11000)
        self.assertEqual(L.balance("cap"), 10000)
        L.verify()


class Imutabilidade(unittest.TestCase):
    def test_sem_api_de_edicao_ou_exclusao(self):
        for nome in ("edit", "update", "delete", "remove", "amend"):
            self.assertFalse(hasattr(Ledger, nome))

    def test_objetos_congelados(self):
        L = make()
        e = L.post("a", d1, "x", [D("cx", 1), C("cap", 1)])
        with self.assertRaises(dataclasses.FrozenInstanceError):
            e.description = "outra"
        with self.assertRaises(dataclasses.FrozenInstanceError):
            e.postings[0].amount = 999
        with self.assertRaises(AttributeError):
            e.postings.append(D("cx", 1))

    def test_journal_exposto_e_copia(self):
        L = make()
        L.post("a", d1, "x", [D("cx", 1), C("cap", 1)])
        self.assertIsInstance(L.journal, tuple)
        with self.assertRaises(TypeError):
            L.accounts["cx"] = None

    def test_cadeia_de_hashes(self):
        L = make()
        a = L.post("a", d1, "x", [D("cx", 1), C("cap", 1)])
        b = L.post("b", d1, "y", [D("cx", 2), C("cap", 2)])
        self.assertEqual(a.prev_hash, GENESIS)
        self.assertEqual(b.prev_hash, a.hash)
        self.assertEqual(L.head, b.hash)
        L.verify()

    def test_adulteracao_detectada(self):
        L = make()
        L.post("a", d1, "x", [D("cx", 100), C("cap", 100)])
        L.post("b", d1, "y", [D("cx", 2), C("cap", 2)])
        L._journal[0] = dataclasses.replace(L._journal[0], postings=(D("cx", 1), C("cap", 1)))
        with self.assertRaises(TamperedLedger):
            L.verify()

    def test_remocao_detectada(self):
        L = make()
        for k in "abc":
            L.post(k, d1, k, [D("cx", 1), C("cap", 1)])
        del L._journal[1]
        with self.assertRaises(TamperedLedger):
            L.verify()


class Idempotencia(unittest.TestCase):
    def test_retry_nao_duplica(self):
        L = make()
        e1 = L.post("NF-1", d1, "compra", [D("cx", 10), C("cap", 10)])
        e2 = L.post("NF-1", d1, "compra", [D("cx", 10), C("cap", 10)])
        self.assertIs(e1, e2)
        self.assertEqual(len(L.journal), 1)
        self.assertEqual(L.balance("cx"), 10)

    def test_ordem_das_partidas_nao_importa(self):
        L = make()
        e1 = L.post("k", d1, "x", [D("cx", 10), C("cap", 10)])
        e2 = L.post("k", d1, "x", [C("cap", 10), D("cx", 10)])
        self.assertIs(e1, e2)

    def test_mesma_key_conteudo_diferente(self):
        L = make()
        L.post("NF-1", d1, "compra", [D("cx", 10), C("cap", 10)])
        for args in [
            (d1, "compra", [D("cx", 11), C("cap", 11)]),   # valor
            (d2, "compra", [D("cx", 10), C("cap", 10)]),   # data
            (d1, "outra", [D("cx", 10), C("cap", 10)]),    # histórico
            (d1, "compra", [D("cx", 10), C("rec", 10)]),   # conta
        ]:
            with self.assertRaises(IdempotencyConflict):
                L.post("NF-1", *args)
        self.assertEqual(len(L.journal), 1)

    def test_conta_idempotente(self):
        L = make()
        L.open_account("cx", "Caixa", A.ATIVO)
        with self.assertRaises(IdempotencyConflict):
            L.open_account("cx", "Caixa", A.PASSIVO)


class Estorno(unittest.TestCase):
    def test_estorno_compensatorio(self):
        L = make()
        L.post("a", d1, "x", [D("cx", 50), C("cap", 50)])
        r = L.reverse("a", d2, "erro")
        self.assertEqual(r.reverses, "a")
        self.assertEqual(r.key, "estorno:a")
        self.assertEqual([p.amount for p in r.postings], [-50, 50])
        self.assertEqual(L.balance("cx"), 0)
        self.assertEqual(len(L.journal), 2)          # original preservado
        self.assertEqual(L.get("a").postings[0].amount, 50)
        self.assertTrue(L.is_reversed("a"))
        self.assertIs(L.reversal_of("a"), r)
        L.verify()

    def test_saldo_historico_preservado(self):
        L = make()
        L.post("a", d1, "x", [D("cx", 50), C("cap", 50)])
        L.reverse("a", d2)
        self.assertEqual(L.balance("cx", as_of=d1), 50)
        self.assertEqual(L.balance("cx", as_of=d2), 0)

    def test_estorno_idempotente(self):
        L = make()
        L.post("a", d1, "x", [D("cx", 50), C("cap", 50)])
        r1 = L.reverse("a", d2, "erro")
        r2 = L.reverse("a", d2, "erro")
        self.assertIs(r1, r2)
        self.assertEqual(len(L.journal), 2)

    def test_nao_estorna_duas_vezes(self):
        L = make()
        L.post("a", d1, "x", [D("cx", 50), C("cap", 50)])
        L.reverse("a", d2)
        with self.assertRaises(ReversalError):
            L.reverse("a", d2, key="outro-estorno")
        with self.assertRaises(IdempotencyConflict):
            L.reverse("a", d2, "motivo diferente")

    def test_nao_estorna_estorno(self):
        L = make()
        L.post("a", d1, "x", [D("cx", 50), C("cap", 50)])
        L.reverse("a", d2)
        with self.assertRaises(ReversalError):
            L.reverse("estorno:a", d2)

    def test_estorno_de_inexistente(self):
        with self.assertRaises(LedgerError):
            make().reverse("nao-existe", d1)


if __name__ == "__main__":
    unittest.main()
