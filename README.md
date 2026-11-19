# ledger-1494

Ledger de partidas dobradas básico, no modelo de Pacioli (1494). Em memória, sem banco, sem API, sem dependências.

- **Contas** com natureza (Ativo, Passivo, Patrimônio, Receita, Despesa)
- **Lançamentos** com ≥ 2 partidas; débito = `+`, crédito = `−`
- **Regra da soma zero**: lançamento só entra se a soma das partidas for 0
- **Saldo derivado**: contas não guardam saldo; é calculado a partir das partidas
- **Append-only**: correção por estorno (`reverse`)
- Valores em centavos (`int`)

```bash
python3 ledger.py               # demo + balancete
python3 -m unittest test_ledger # testes
```
