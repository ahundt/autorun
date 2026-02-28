# autorun demo — VHS tape for reproducible headless recording
#
# VHS creates its own virtual terminal — no WINDOWID needed (unlike t-rec).
#
# Install:  go install github.com/charmbracelet/vhs@latest
#           OR brew install vhs  (requires Xcode license: sudo xcodebuild -license accept)
# Record:   cd ~/.claude/autorun && vhs plugins/autorun/demo/autorun_demo.tape
# Output:   autorun_demo.gif + autorun_demo.mp4  (in current directory)

Output autorun_demo.gif
Output autorun_demo.mp4

Set Theme "Dracula"
Set FontFamily "JetBrains Mono"
Set FontSize 20
Set Width 1200
Set Height 700
Set Padding 24
Set TypingSpeed 60ms

# Run the demo script (--play mode: no live API calls, $0.00 cost)
# VHS starts a fresh shell — navigate to repo root first
Type "cd ~/.claude/autorun"
Enter
Sleep 1s

Type "uv run python plugins/autorun/tests/test_demo.py --play"
Enter

# The demo runs all 7 acts in --play mode (hook calls only, no API cost).
# Hook subprocess calls complete in ~18-30s total. Sleep 60s covers it with margin.
Sleep 60s
