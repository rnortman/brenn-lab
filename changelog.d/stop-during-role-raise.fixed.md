- A SIGINT or SIGTERM that arrives while a role is handling a command now
  stops the script once the command ends, also when the role raised instead
  of replying. The script sees the stop, not the role's error, so a script
  that catches the role's error no longer runs on past the stop.
