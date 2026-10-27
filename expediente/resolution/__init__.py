"""Entity resolution.

Two suppliers with different RUTs are legitimately two suppliers. The interesting
fact is rarely "these are the same company" — it is "these two companies share a
person or an address". So this package produces **relations**, never merges.
"""
