# ledger-1494

Ledger de partidas dobradas básico, no modelo de Pacioli (1494). Em memória, sem banco, sem API, sem dependências.

- **Contas** com natureza (Ativo, Passivo, Patrimônio, Receita, Despesa)
- **Lançamentos** com ≥ 2 partidas; débito = `+`, crédito = `−`
- **Regra da soma zero**: lançamento só entra se a soma das partidas for 0
- **Saldo derivado**: contas não guardam saldo; é calculado a partir das partidas
- **Append-only**: correção por estorno (`reverse`)
- Valores em centavos (`int`)

## Versões

| Versão | Pasta | O que é |
|---|---|---|
| v1 | raiz | Ledger básico: contas, lançamentos, soma zero, saldo derivado |
| v2 | [`v2/`](v2/) | **O ledger que não mente**: imutabilidade com cadeia de hashes, idempotência por chave de negócio, estorno por lançamento compensatório |

```bash
python3 ledger.py && python3 -m unittest test_ledger                  # v1
cd v2 && python3 ledger.py && python3 -m unittest test_ledger         # v2
```
