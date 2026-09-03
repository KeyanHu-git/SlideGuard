# Contributing

Open an issue before changing a fidelity rule or threshold. A bug fix needs a small failing fixture or fault injection, the validator that catches it, and a note in `docs/reproduction-and-pitfalls.md`.

Run:

```powershell
py -m pip install -e ".[qa,dev]"
py -m pytest -q tests
slideguard doctor
slideguard fixtures --out .tmp\fixtures
slideguard export .tmp\fixtures\slideguard-core-torture.pptx --slides all --out .tmp\fixture-output
```

Never commit a user's PPTX, exported paper figure or report containing an absolute private path. Hosted CI covers unit and fault tests. PowerPoint integration results should be attached to the issue from a controlled Windows machine.

