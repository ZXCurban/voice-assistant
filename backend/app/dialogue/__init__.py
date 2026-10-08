"""Dialogue management on top of the NLU: state machine + backend actions.

NLU (intent + slots) → DialogueManager (what to ask / do next) → tools
(AssistantOrchestrator) → templated reply. The replies reproduce the
phrasing the NLU models were trained on, because the previous reply is a
model input (see app.nlu.contract.build_model_input).
"""
