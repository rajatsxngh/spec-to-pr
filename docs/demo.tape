# VHS script for the terminal part of the launch demo (see docs/demo.md).
# Edit the issue URL and repo path, then run: vhs docs/demo.tape
Output docs/img/demo.gif

Set Shell "bash"
Set FontSize 18
Set Width 1300
Set Height 760
Set TypingSpeed 40ms
Set Theme "GitHub Light"

Type "uv run spec-to-pr run https://github.com/YOU/demo/issues/1 --repo ~/demo"
Sleep 600ms
Enter

# Wait for the plan, then approve it.
Wait+Screen /Approve, edit or reject/
Sleep 4s
Type "a"
Enter

# Wait for the draft PR line.
Wait+Screen@10m /Opened draft PR/
Sleep 5s
