{
  description = "Personal reusable GitHub Actions";

  inputs.nixpkgs.url = "github:nixos/nixpkgs?ref=nixos-unstable";

  outputs = { nixpkgs, ... }:
    let
      supportedSystems = [ "x86_64-linux" "aarch64-darwin" "x86_64-darwin" "aarch64-linux" ];
      forEachSupportedSystem = f: nixpkgs.lib.genAttrs supportedSystems (system: f {
        pkgs = import nixpkgs { inherit system; };
      });
    in {
      devShells = forEachSupportedSystem ({ pkgs }: {
        default = pkgs.mkShell {
          nativeBuildInputs = with pkgs; [
            actionlint
            bash
            git
            jq
            nodejs_24
            pre-commit
            renovate
            shellcheck
            shfmt
            yamllint
            yamlfix
            zizmor
            (python3.withPackages (ps: [ ps.pyyaml ]))
          ];
        };
      });
    };
}
