"""Programme 1's learner (plan §4, step 10; PATHWAYS H; PREREGISTRATION D-24
§2's exploratory track): the trainer that reads the controller plugin's
transition log, prices each level's attributed cost, trains one masked
double DQN per agent, and exports weights the plugin's learned mode reads
(controller/mlp.h).

It lives beside the retired protocol-v2 stack (server.py, multilevel.py,
agent.py, model.py), which stays until plan step 12 (WP10)."""
