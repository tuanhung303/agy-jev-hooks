"""Prompt arms for the Jev bench. An arm overrides any of `question`, `true`, `false` (the gateway
question and its criteria) and `build_query` (the hook's state string); what it leaves out comes
from the working tree. The empty arm `shipped` sends exactly what the live hook sends.

Add an arm here, run it next to `shipped`, and ship it only if it beats `shipped` on mean AUC in
every run without losing top-8 hits (run-to-run noise is about 0.01-0.02 AUC).
"""

ARMS = {
    "shipped": {},
    # The false criterion before the component / older-copy boundary (criterion-2).
    "criterion-2": {
        "false": "`excerpt` only mentions a searched term, or belongs to a different feature that happens to "
                 "use the same word.",
    },
}
