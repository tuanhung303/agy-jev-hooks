"""sage.jev - Jev decision-model call package (3-call clean break).

Layout:
  jev.yaml     one config file: cases + routing (categories, axes, pairs) + skills
  transport.py shared _call_jev HTTP transport (spec-v4 headers, one 503 retry)
  config/      catalog.py (category -> route/axis, derive_verdicts), hermetic
               yaml_lite.py + yaml_scalars.py (hook HOME isolation drops
               user-site PyYAML; tests cross-check it against PyYAML)
  request/     parser.py builds spec-v4 request bodies from jev.yaml cases;
               prompt_pair.py extracts the last user/agent pair minus steering
  evidence/    assemble.py five-block turn evidence; context.py bounded
               user-requirements extraction; redact.py bounded secret
               redaction; tools.py tool-arg normalization; blast.py
               deterministic AST blast-radius detection
  verdict/     compass.py stop-time label routing: pass / failed[reasons]

All three Jev calls (router, compass, verifier) are wired; see
tmp/jev-three-call-clean-break-v2.md.
"""
