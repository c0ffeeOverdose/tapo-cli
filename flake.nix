{
  description = "Control Matter-era Tapo devices through TP-Link's cloud";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    {
      self,
      nixpkgs,
    }:
    let
      version = "0.1.0";
      eachSystem = f: nixpkgs.lib.genAttrs nixpkgs.lib.systems.flakeExposed (system: f (import nixpkgs { inherit system; }));
    in
    {
      # `nix run .` works through meta.mainProgram, so no `apps` output is needed.
      packages = eachSystem (pkgs: rec {
        tapo-cli = pkgs.python3Packages.buildPythonApplication {
          pname = "tapo-cli";
          inherit version;
          pyproject = true;
          src = self;

          build-system = [ pkgs.python3Packages.setuptools ];
          dependencies = [ pkgs.python3Packages.requests ];
          nativeCheckInputs = [ pkgs.python3Packages.pytestCheckHook ];
          pythonImportsCheck = [ "tapo_cli" ];

          meta = {
            description = "Control Matter-era Tapo devices through TP-Link's cloud";
            homepage = "https://github.com/c0ffeeOverdose/tapo-cli";
            license = pkgs.lib.licenses.mit;
            mainProgram = "tapo-cli";
          };
        };
        default = tapo-cli;
      });

      devShells = eachSystem (pkgs: {
        default = pkgs.mkShellNoCC {
          packages = [
            (pkgs.python3.withPackages (ps: [
              ps.pytest
              ps.requests
            ]))
          ];
        };
      });
    };
}
