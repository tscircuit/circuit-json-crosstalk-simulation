"""Source identity/contact guards adapted from tscircuit/simulate-return-current (MIT)."""
import gmsh
import json


def mapped_cleanup(groups, port_groups, volume_tolerance=1e-5, audit_path=None):
    old = sorted(set().union(*groups.values()))
    assert set(old) == {t for _, t in gmsh.model.getEntities(3)}, 'Unowned CAD volume'
    assert all(groups.values()), 'Empty material/net group'
    for name, tags in groups.items():
        for other, theirs in groups.items():
            if name < other and tags & theirs:
                raise ValueError('One CAD volume already belongs to different materials/nets')
    masses = {name: sum(gmsh.model.occ.getMass(3, tag) for tag in tags)
              for name, tags in groups.items()}
    source_geometry = {str(tag): {'massMm3': gmsh.model.occ.getMass(3, tag),
                                  'boundsMm': list(gmsh.model.getBoundingBox(3, tag))} for tag in old}
    port_tags = sorted(set().union(*port_groups))
    port_mass = [sum(gmsh.model.occ.getMass(2, tag) for tag in tags) for tags in port_groups]
    _, mapping = gmsh.model.occ.fragment([(3, t) for t in old], [(2, t) for t in port_tags])
    gmsh.model.occ.synchronize()
    assert len(mapping) == len(old) + len(port_tags), 'Missing CAD fragmentation map'
    volume_map = {tag: {new for dim, new in row if dim == 3}
                  for tag, row in zip(old, mapping[:len(old)])}
    surface_map = {tag: {new for dim, new in row if dim == 2}
                   for tag, row in zip(port_tags, mapping[len(old):])}
    diagnostic = {'status': 'checking_source_identity', 'sourceGeometry': source_geometry,
                  'sourceGroups': {k: sorted(v) for k, v in groups.items()},
                  'volumeTagMap': {str(k): sorted(v) for k, v in volume_map.items()},
                  'portSurfaceTagMap': {str(k): sorted(v) for k, v in surface_map.items()}}
    if audit_path:
        audit_path.write_text(json.dumps(diagnostic, indent=2) + '\n')
    if any(not row for row in volume_map.values()) or any(not row for row in surface_map.values()):
        raise ValueError('CAD cleanup lost a source volume or port')
    remapped = {name: set().union(*(volume_map[t] for t in tags)) for name, tags in groups.items()}
    for name, tags in remapped.items():
        for other, theirs in remapped.items():
            if name < other and tags & theirs:
                conflicts = sorted(tags & theirs)
                diagnostic.update(status='rejected_group_identity_overlap', conflictGroups=[name, other],
                                  conflictVolumes={str(t): {'massMm3': gmsh.model.occ.getMass(3, t),
                                                   'boundsMm': list(gmsh.model.getBoundingBox(3, t))} for t in conflicts})
                if audit_path:
                    audit_path.write_text(json.dumps(diagnostic, indent=2) + '\n')
                raise ValueError(f'CAD cleanup merged different source identities: {name}, {other}, volumes {conflicts}')
    alive = {t for _, t in gmsh.model.getEntities(3)}
    if set().union(*remapped.values()) != alive:
        raise ValueError('CAD cleanup left an unowned volume')
    after = {name: sum(gmsh.model.occ.getMass(3, tag) for tag in tags)
             for name, tags in remapped.items()}
    if any(abs(after[name] - value) > volume_tolerance for name, value in masses.items()):
        raise ValueError('CAD cleanup changed a source material/net volume')
    ports = [set().union(*(surface_map[t] for t in tags)) for tags in port_groups]
    for i, tags in enumerate(ports):
        if any(tags & other for other in ports[i + 1:]):
            raise ValueError('CAD cleanup merged independently defined ports')
        if abs(sum(gmsh.model.occ.getMass(2, tag) for tag in tags) - port_mass[i]) > 1e-8:
            raise ValueError('CAD cleanup changed a source port area')
    audit = {'method': 'Second Boolean fragmentation with returned source-to-output map',
             'sourceVolumeTags': old, 'volumeTagMap': {str(k): sorted(v) for k, v in volume_map.items()},
             'portSurfaceTagMap': {str(k): sorted(v) for k, v in surface_map.items()},
             'sourceGroupVolumesMm3': masses, 'outputGroupVolumesMm3': after,
             'maximumAllowedGroupVolumeErrorMm3': volume_tolerance,
             'sourcePortAreasMm2': port_mass,
             'groupTags': {name: sorted(tags) for name, tags in remapped.items()}}
    return remapped, ports, audit


def verify_port_contacts(port, surfaces, copper_by_net, diagnostic_path=None):
    def edges(faces):
        return {t for d, t in gmsh.model.getBoundary([(2, t) for t in faces],
                                                   combined=False, oriented=False) if d == 1}
    port_edges = edges(surfaces)
    width = port['width_mm']
    expected_area = width * abs(port['signal_z_mm'] - port['reference_z_mm'])
    if abs(sum(gmsh.model.occ.getMass(2, t) for t in surfaces) - expected_area) > 1e-8:
        raise ValueError('Port aperture area differs from intended signal/reference gap')
    lengths = {}
    embedded = []
    for side, net in [('signal', port['signal_net']), ('reference', port['reference_net'])]:
        faces = {t for d, t in gmsh.model.getBoundary([(3, t) for t in copper_by_net[net]],
                                                    combined=True, oriented=False) if d == 2}
        intended_z = port['signal_z_mm'] if side == 'signal' else port['reference_z_mm']
        ends = {tag for tag in port_edges if (lambda bounds:
                  abs((bounds[2] + bounds[5]) / 2 - intended_z) < 2e-7
                  and bounds[5] - bounds[2] < 4e-7)(gmsh.model.getBoundingBox(1, tag))}
        shared = ends & edges(faces)
        for curve in sorted(ends - shared):
            lo, hi = gmsh.model.getParametrizationBounds(1, curve)
            samples = [float(lo[0] + f * (hi[0] - lo[0])) for f in [.1, .3, .5, .7, .9]]
            coordinates = gmsh.model.getValue(1, curve, samples)
            candidates = []
            face_checks = []
            for face in faces:
                bounds = gmsh.model.getBoundingBox(2, face)
                if abs((bounds[2] + bounds[5]) / 2 - intended_z) < 2e-7 and bounds[5] - bounds[2] < 4e-7:
                    inside = gmsh.model.isInside(2, face, coordinates)
                    face_checks.append({'face': face, 'bounds': list(bounds), 'insideSampleCount': inside})
                    if inside == len(samples):
                        candidates.append(face)
            if len(candidates) != 1:
                if diagnostic_path:
                    diagnostic_path.write_text(json.dumps({'port': port, 'side': side, 'curve': curve,
                        'curveBounds': list(gmsh.model.getBoundingBox(1, curve)), 'sampleCoordinates': coordinates.tolist(),
                        'matchingFaces': candidates, 'faceChecks': face_checks,
                        'allConductorFaceBounds': {str(t): list(gmsh.model.getBoundingBox(2,t)) for t in faces}}, indent=2)+'\n')
                raise ValueError(f'Port {port["index"]} lacks one physical {side} face under its contact')
            # An open line ending inside a broad power plane does not split its
            # CAD face. Embed the literal port-end curve so both meshes use the
            # same nodes; geometric ownership was checked before this operation.
            gmsh.model.mesh.embed(1, [curve], 2, candidates[0])
            embedded.append({'side': side, 'curve': curve, 'conductorFace': candidates[0]})
            shared.add(curve)
        length = sum(gmsh.model.occ.getMass(1, t) for t in shared)
        if abs(length - width) > 2e-6:
            raise ValueError(f'Port {port["index"]} lost its physical {side} conductor contact')
        lengths[side + 'ContactLengthMm'] = length
    return {'portIndex': port['index'], 'apertureAreaMm2': expected_area,
            'embeddedContactCurves': embedded, **lengths}
