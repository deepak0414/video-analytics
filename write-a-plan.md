# Develop a plan to create video analytics platform
This is a document to create a plan (plan for plan.md) for creatin video analytics platform.
We have created a rough architecture document (`video-analytics-solution-architecture.md`) and a `video-analytics-model-analysis.md` document (this lists all the available models and how they fit into various roles in `video-analytics-solution-architecture.md`).

First level of plan here is to re-use as much as existing aritfact (nvidia or elsewhere and open models). Create a small harnesses to indepdently test each part of the solution.
Right now we can focus on using scripts (like python, etc) to drive our harness with larger goal of converting them to rust or c++ based codebase (for performance). However since first goal is to prove the proovability of concept, let's focus on easy scriptability.

Create a plan to implement this solution and break down into smaller independent components which can be easily tested independently and can be integrated with each other easily as well for full solution.