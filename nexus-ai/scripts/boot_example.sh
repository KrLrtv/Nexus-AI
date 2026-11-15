#!/data/data/com.termux/files/usr/bin/bash
# Copy to ~/.termux/boot/ only after Termux:Boot install and setup.
# Load secrets from your own private config (chmod 600), not from world-readable files.
source "$HOME/.config/nexus-ai/secrets.sh"
cd "$HOME/NEXUS_AI_1_0_final_source"
exec bash scripts/start_termux.sh
