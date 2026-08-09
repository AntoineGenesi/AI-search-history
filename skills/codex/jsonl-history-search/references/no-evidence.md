# Weak Or No Evidence

No evidence means `not_found_in_searched_scope`, not absent everywhere.

Inspect the searched roots, date/source filters, file and byte caps, missing
roots, and required next reads. If the result reports a complete bounded scope
with no cap hit, no required read, and `terminal_answer_and_stop`, answer that
scope and run nothing else. Otherwise name the blind spot and use only its
bounded continuation. Never substitute unrelated local files.
