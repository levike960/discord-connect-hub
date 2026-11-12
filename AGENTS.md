# Architecture rules

- Keep craft weight and batching calculations in a dependency-free Python module; this allows concrete capacity tests without changing warehouse transactions.
- Keep trip progress in browser session storage, scoped to the signed-in user and exact plan; marking a trip is a planning action, not a stock transaction.
- Add product weights through the existing additive startup migration; existing records must remain intact.