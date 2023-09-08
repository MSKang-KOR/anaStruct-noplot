import math
from typing import TYPE_CHECKING, List, Tuple

import numpy as np

from anastruct.fem.elements import det_axial, det_moment, det_shear

if TYPE_CHECKING:
    from anastruct.fem.elements import Element
    from anastruct.fem.system import SystemElements


def set_force_vector(
    system: "SystemElements", force_list: List[Tuple[int, int, float]]
) -> np.ndarray:
    """
    :param force_list: list containing tuples with the
    1. number of the node,
    2. the number of the direction (1 = x, 2 = z, 3 = y)
    3. the force
    [(1, 3, 1000)] node=1, direction=3 (y), force=1000
    list may contain multiple tuples
    :return: Vector with forces on the nodes
    """
    assert system.system_force_vector is not None
    for id_, direction, force in force_list:
        system.system_force_vector[(id_ - 1) * 3 + direction - 1] += force

    return system.system_force_vector


def prep_matrix_forces(system: "SystemElements") -> None:
    system.system_force_vector = system.system_force_vector = np.zeros(
        len(system._vertices) * 3
    )
    apply_perpendicular_q_load(system)
    apply_parallel_qn_load(system)
    apply_point_load(system)
    apply_moment_load(system)


def apply_moment_load(system: "SystemElements") -> None:
    for node_id, Ty in system.loads_moment.items():
        set_force_vector(system, [(node_id, 3, Ty)])


def apply_point_load(system: "SystemElements") -> None:
    for node_id in system.loads_point:
        Fx, Fz = system.loads_point[node_id]
        # system force vector.
        set_force_vector(
            system,
            [
                (node_id, 1, Fx),
                (node_id, 2, Fz),
            ],
        )


def apply_perpendicular_q_load(system: "SystemElements") -> None:
    for element_id in system.loads_dead_load:
        element = system.element_map[element_id]
        qi_perpendicular = element.all_qp_load[0]
        q_perpendicular = element.all_qp_load[1]
        if q_perpendicular == 0 and qi_perpendicular == 0:
            continue

        kl = element.constitutive_matrix[1][1] * 1e6
        kr = element.constitutive_matrix[2][2] * 1e6

        # minus because of systems positive rotation
        left_moment = det_moment(
            kl, kr, qi_perpendicular, q_perpendicular, 0, element.EI, element.l
        )
        right_moment = -det_moment(
            kl, kr, qi_perpendicular, q_perpendicular, element.l, element.EI, element.l
        )
        rleft = det_shear(
            kl, kr, qi_perpendicular, q_perpendicular, 0, element.EI, element.l
        )
        rright = -det_shear(
            kl, kr, qi_perpendicular, q_perpendicular, element.l, element.EI, element.l
        )

        rleft_x = rleft * math.sin(element.a1)
        rright_x = rright * math.sin(element.a2)

        rleft_z = rleft * math.cos(element.a1)
        rright_z = rright * math.cos(element.a2)

        if element.type == "truss":
            left_moment = 0
            right_moment = 0

        primary_force = np.array(
            [rleft_x, rleft_z, left_moment, rright_x, rright_z, right_moment]
        )
        element.element_primary_force_vector -= primary_force

        # Set force vector
        assert system.system_force_vector is not None
        system.system_force_vector[
            (element.node_id1 - 1) * 3 : (element.node_id1 - 1) * 3 + 3
        ] += primary_force[0:3]
        system.system_force_vector[
            (element.node_id2 - 1) * 3 : (element.node_id2 - 1) * 3 + 3
        ] += primary_force[3:]


def apply_parallel_qn_load(system: "SystemElements") -> None:
    for element_id in system.loads_dead_load:
        element = system.element_map[element_id]
        qni_parallel = element.all_qn_load[0]
        qn_parallel = element.all_qn_load[1]
        if qn_parallel == 0 and qni_parallel == 0:
            continue

        # minus because of systems positive rotation
        rleft = -det_axial(qni_parallel, qn_parallel, 0, element.EA, element.l)
        rright = det_axial(qni_parallel, qn_parallel, element.l, element.EA, element.l)

        rleft_x = -rleft * math.cos(element.a1)
        rright_x = -rright * math.cos(element.a2)

        rleft_z = rleft * math.sin(element.a1)
        rright_z = rright * math.sin(element.a2)

        element.element_primary_force_vector[0] -= rleft_x
        element.element_primary_force_vector[1] -= rleft_z
        element.element_primary_force_vector[3] -= rright_x
        element.element_primary_force_vector[4] -= rright_z

        set_force_vector(
            system,
            [
                (element.node_1.id, 2, rleft_z),
                (element.node_2.id, 2, rright_z),
                (element.node_1.id, 1, rleft_x),
                (element.node_2.id, 1, rright_x),
            ],
        )


def dead_load(system: "SystemElements", g: float, element_id: int) -> None:
    system.loads_dead_load.add(element_id)
    system.element_map[element_id].dead_load = g


def assemble_system_matrix(
    system: "SystemElements", validate: bool = False, geometric_matrix: bool = False
) -> None:
    """
    Shape of the matrix = n nodes * n d.o.f.
    Shape = n * 3
    """
    system._remainder_indexes = []
    if not geometric_matrix:
        shape = len(system.node_map) * 3
        system.shape_system_matrix = shape
        system.system_matrix = np.zeros((shape, shape))

    assert system.system_matrix is not None
    for matrix_index, K in system.system_spring_map.items():
        #  first index is row, second is column
        system.system_matrix[matrix_index][matrix_index] += K

    # Determine the elements location in the stiffness matrix.
    # system matrix [K]
    #
    # [fx 1] [K         |  \ node 1 starts at row 1
    # |fz 1] |  K       |  /
    # |Ty 1] |   K      | /
    # |fx 2] |    K     |  \ node 2 starts at row 4
    # |fz 2] |     K    |  /
    # |Ty 2] |      K   | /
    # |fx 3] |       K  |  \ node 3 starts at row 7
    # |fz 3] |        K |  /
    # [Ty 3] [         K] /
    #
    #         n   n  n
    #         o   o  o
    #         d   d  d
    #         e   e  e
    #         1   2  3
    #
    # thus with appending numbers in the system matrix: column = row

    for i in range(len(system.element_map)):
        element = system.element_map[i + 1]
        element_matrix = element.stiffness_matrix

        # n1 and n2 are starting indexes of the rows and the columns for node 1 and node 2
        n1 = (element.node_1.id - 1) * 3
        n2 = (element.node_2.id - 1) * 3
        system.system_matrix[n1 : n1 + 3, n1 : n1 + 3] += element_matrix[0:3, :3]
        system.system_matrix[n1 : n1 + 3, n2 : n2 + 3] += element_matrix[0:3, 3:]

        system.system_matrix[n2 : n2 + 3, n1 : n1 + 3] += element_matrix[3:6, :3]
        system.system_matrix[n2 : n2 + 3, n2 : n2 + 3] += element_matrix[3:6, 3:]

    # returns True if symmetrical.
    if validate:
        assert np.allclose((system.system_matrix.transpose()), system.system_matrix)


def set_displacement_vector(
    system: "SystemElements", nodes_list: List[Tuple[int, int]]
) -> np.ndarray:
    """
    :param nodes_list: list containing tuples with
    1.the node
    2. the d.o.f. that is set
    :return: Vector with the displacements of the nodes (If displacement is not known,
             the value is set
    to NaN)
    """
    if system.system_displacement_vector is None:
        system.system_displacement_vector = np.ones(len(system._vertices) * 3) * np.NaN

    for i in nodes_list:
        index = (i[0] - 1) * 3 + i[1] - 1

        try:
            system.system_displacement_vector[index] = 0
        except IndexError as e:
            raise IndexError(
                e,
                "This often occurs if you set supports before the all the elements are modelled. "
                "First finish the model.",
            ) from e
    return system.system_displacement_vector


def process_conditions(system: "SystemElements") -> None:
    indexes = []
    # remove the unsolvable values from the matrix and vectors
    assert system.shape_system_matrix is not None
    assert system.system_displacement_vector is not None
    for i in range(system.shape_system_matrix):
        if system.system_displacement_vector[i] == 0:
            indexes.append(i)
        else:
            system._remainder_indexes.append(i)

    system.system_displacement_vector = np.delete(
        system.system_displacement_vector, indexes, 0
    )
    system.reduced_force_vector = np.delete(system.system_force_vector, indexes, 0)
    system.reduced_system_matrix = np.delete(system.system_matrix, indexes, 0)
    system.reduced_system_matrix = np.delete(system.reduced_system_matrix, indexes, 1)


def process_supports(system: "SystemElements") -> None:
    for node in system.supports_hinged:
        set_displacement_vector(system, [(node.id, 1), (node.id, 2)])

    for i, supports_rolli in enumerate(system.supports_roll):
        if not system.supports_roll_rotate[i]:
            set_displacement_vector(
                system,
                [
                    (supports_rolli.id, system.supports_roll_direction[i]),
                    (supports_rolli.id, 3),
                ],
            )
        else:
            set_displacement_vector(
                system,
                [(supports_rolli.id, system.supports_roll_direction[i])],
            )

    for node in system.supports_rotational:
        set_displacement_vector(system, [(node.id, 3)])

    for node in system.internal_hinges:
        set_displacement_vector(system, [(node.id, 3)])
        for el in system.node_element_map[node.id]:
            el.compile_constitutive_matrix(
                el.EA, el.EI, el.l, el.springs, el.node_1.hinge, el.node_2.hinge
            )
            el.compile_stiffness_matrix()

    for node in system.supports_fixed:
        set_displacement_vector(system, [(node.id, 1), (node.id, 2), (node.id, 3)])

    for node, roll in system.supports_spring_x:
        if not roll:
            set_displacement_vector(system, [(node.id, 2)])

    for node, roll in system.supports_spring_z:
        if not roll:
            set_displacement_vector(system, [(node.id, 1)])

    for node, roll in system.supports_spring_y:
        if not roll:
            set_displacement_vector(system, [(node.id, 1), (node.id, 2)])

    for node_id, angle in system.inclined_roll.items():
        for el in system.node_element_map[node_id]:
            if el.node_1.id == node_id:
                el.a1 = el.angle + angle
            elif el.node_2.id == node_id:
                el.a2 = el.angle + angle

            el.compile_kinematic_matrix(el.a1, el.a2, el.l)
            el.compile_stiffness_matrix()