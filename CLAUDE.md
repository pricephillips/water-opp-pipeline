# water-opp-pipeline: session rules

## Written text: the humanizer pass

Every piece of prose a session writes that a client, Price or the public could read gets a humanizer pass before it is sent, committed or published: invoke the `humanizer` skill (`/humanizer`) on it. The same rule is in every Hawthorn Intelligence repository. It covers:

- Reports: client reports, case studies, location reports, briefs, scorecards, updates, and written reports for Price.
- End-of-pass reports: the closing reply that ends a pass or a session's work (files changed, commands, PR links, decisions for Price).
- Pull request bodies and commit messages.
- Client-facing and public copy: page text, email templates and subjects, alert wording, analyst notes, chart titles and captions, and READMEs and docs that someone outside Hawthorn could read. In this repository: `README.md`, `docs/`, and the page text in `map/water-opp-map.html`, `docs/index.html` and `docs/water-opp-map.html`.
- Free-text fields a reader sees in hand-drafted data rows (summaries, descriptions, notes), including rows a helper agent drafted. Helper agents cannot run skills, so the session that commits the rows runs the pass.

The pass changes wording only:

- Prose only. Leave tables, headings, quoted text (a client's own framework or rubric wording, quotes from sources), numbers, dates, FIPS codes, outcome codes and their display labels, defined labels and term names, and URLs untouched. In code, templates and config, change only the human-readable strings; tags, keys, ids and placeholders (`{{ name }}`, `${x}`, `%s`) stay exactly as they are.
- Nothing new. No new claims, opinions, first person or flourishes, and nothing causal. Keep the text's own register (plain third person in client reports) and every defined-term parenthetical.
- New and changed text only. The pass is not a sweep of text already in the repository.
- When no voice fingerprint, overrides file or voice sample is found (the usual case in a cloud session), run at the skill's default strength, say so in its pre-flight line, and carry on without asking.
- Check that no number, URL or outcome code changed. Before the pass, copy the text to the scratchpad as `before.md` (for strings in a file, copy the file); after it, run this on the pair, with `report.md` standing for the edited text or file. It must print nothing, and anything it prints goes back as it was:
  `diff <(grep -oE 'https?://[^ )>]+|[0-9]+([.,][0-9]+)*|advanced_confirmed|restricted_conditional|blocked_confirmed|pending' before.md | sort) <(grep -oE 'https?://[^ )>]+|[0-9]+([.,][0-9]+)*|advanced_confirmed|restricted_conditional|blocked_confirmed|pending' report.md | sort)`

By kind:

- End-of-pass reports: write the draft to the scratchpad as `report.md`, run the pass and the check, then send the result as the reply. Commands, code blocks, file paths and PR links stay exactly as written.
- Pull request bodies: write the body to the scratchpad as `report.md`, run the pass and the check, then open the pull request from that file (`gh api repos/{owner}/{repo}/pulls -f head=<branch> -f base=main -f title="<title>" -F body=@report.md`, or paste the file's text into the GitHub tool's body). The same goes for a body edited later.
- Commit messages: write the message to the scratchpad as `report.md`, run the pass and the check, then commit with `git commit -F report.md`. Keep the subject line short, and keep the trailer lines (`Co-Authored-By:`, `Claude-Session:`) exactly as written.
- Page copy, templates and data text: run the pass on the changed strings before the commit, then the repository's usual checks. If a test asserts the old wording, update the test in the same change.
- Client reports: then run the wording checks on the humanized text, and build any Word or PDF file from it.

Not covered: text a script writes at run time (humanize the template it is filled from instead), helper agent reports, other chat replies (answers, status lines, questions), code itself (identifiers, developer comments), and data values that are not prose. If the skill is not available in the session, tell Price in one line; never skip it silently.
