"""Generic reporter for clusters without a site-specific implementation."""

from . import common

CPU_KEY = "cpu_efctv"


def main(args):
    del args

    nodes = common.fetch_nodes()
    if nodes is None:
        return

    hardware = common.group_by_hardware(nodes, CPU_KEY)

    for key in sorted(hardware):
        _, _, gpu_frozenset = key
        common.print_summary(
            common.hw_key_label(key),
            hardware[key],
            len(hardware[key]),
            sorted(dict(gpu_frozenset)),
            cpu_key=CPU_KEY,
        )
