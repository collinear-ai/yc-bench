"""Policy-as-code experiment: ask a model to write a deterministic policy
that drives the yc-bench CLI, optionally consulting a fixed cheap LLM helper
for NLP-flavoured classification (e.g. adversarial-client detection).

Compare models on policy quality rather than turn-level tool use.
"""
