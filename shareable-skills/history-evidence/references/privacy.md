# Privacy

Conversation logs are private by default. Local inspection is allowed only for the current task and approved roots.

## Do Not Expose

Do not publish, commit, upload, or paste externally:

- raw private transcript text;
- absolute private paths;
- local usernames;
- session identifiers;
- secrets, tokens, credentials, email addresses, phone numbers, or private customer data.

## Safer Sharing

For shareable reports, use:

- sanitized source labels instead of raw paths;
- source role categories such as `user_message`, `tool_call`, `tool_output`;
- aggregate counts and cap labels;
- short paraphrases instead of raw transcript excerpts unless the user explicitly authorizes quoting.

## Local Final Answers

When answering the local user, exact local source refs and short excerpts can be useful. Keep excerpts bounded and relevant. If the answer may be copied outside the machine, provide a sanitized version.

## Tooling Boundaries

Do not upload private history files to external services for parsing, summarization, or search unless the user explicitly requests that specific disclosure. Do not create new committed fixtures from raw private logs. If you create temporary local extracts, keep them under an ignored/private location or remove them before finishing.
