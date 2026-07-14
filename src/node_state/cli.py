import argparse

from . import node_resources

PRESET_EXCLUDE_STATES = ["PLANNED", "DRAIN", "MAINTENANCE", "RESERVED", "ALLOCATED", "DOWN"]


def main():
    parser = argparse.ArgumentParser(
        prog="node_state",
        description="Summarize Slurm node resources (CPU, memory, GPUs) grouped by hardware type.",
    )
    parser.add_argument(
        "-x",
        "--exclude-states",
        nargs="*",
        default=None,
        metavar="STATE",
        help="Exclude nodes whose state matches (case-insensitive substring). "
        "Without this flag, no nodes are excluded. Pass the flag with no values "
        f"to use the preset ({' '.join(PRESET_EXCLUDE_STATES)}), or list states explicitly.",
    )

    args = parser.parse_args()
    exclude_states = None
    if args.exclude_states is not None:
        exclude_states = args.exclude_states or PRESET_EXCLUDE_STATES
    node_resources.main(exclude_states=exclude_states)


if __name__ == "__main__":
    main()
