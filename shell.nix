{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  buildInputs = [
    # "Just" a command runner: https://github.com/casey/just
    pkgs.just

    pkgs.poetry
    # Install various versions of python so we can test across them
    pkgs.python39
    pkgs.python310
    pkgs.python311
    pkgs.python312

    # keep this line if you use bash
    pkgs.bashInteractive
  ];
}
