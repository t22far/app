#!/bin/bash
# Run this once in a PythonAnywhere Bash console:
#   bash <(curl -s https://raw.githubusercontent.com/t22far/app/Scanner-App-Main/setup_pythonanywhere.sh)
set -e
cd ~
if [ -d app ]; then cd app && git pull -q; else git clone -q https://github.com/t22far/app.git && cd app; fi
python3 -m pip install --user -q -r requirements.txt
echo
echo "Code is in /home/$USER/app and dependencies are installed."
echo "Now go to the Web tab, click 'Add a new web app', choose 'Manual configuration' and Python 3.10+,"
echo "then set the WSGI file to contain exactly:"
echo
echo "    import sys; sys.path.insert(0, '/home/$USER/app')"
echo "    from wsgi import application"
echo
echo "Set 'Source code' and 'Working directory' to /home/$USER/app, then click Reload."
