from django.shortcuts import render
from django.db.models import Q
from django.views.decorators.cache import cache_page

from collections import defaultdict
from django.conf import settings

import json
import functools

from contactnetwork.models import *
from structure.models import Structure
from protein.models import Protein, ProteinSegment
from residue.models import Residue

Alignment = getattr(__import__('common.alignment_' + settings.SITE_NAME, fromlist=['Alignment']), 'Alignment')

from django.http import JsonResponse, HttpResponse
from collections import OrderedDict


def Interactions(request):
    """
    Show interaction heatmap
    """
    return render(request, 'contactnetwork/interactions.html')

def PdbTreeData(request):
    data = Structure.objects.values(
        'representative',
        'pdb_code__index',
        'protein_conformation__protein__parent__family__parent__parent__parent__name',
        'protein_conformation__protein__parent__family__parent__parent__name',
        'protein_conformation__protein__parent__family__parent__name',
        'protein_conformation__protein__parent__family__name',
        'protein_conformation__protein__parent__family__parent__parent__parent__slug',
        'protein_conformation__protein__parent__family__parent__parent__slug',
        'protein_conformation__protein__parent__family__parent__slug',
        'protein_conformation__protein__parent__family__slug',
        'protein_conformation__state__slug'
        ).exclude(refined=True)

    # TODO: Use ordereddict
    l = lambda:defaultdict(l)
    data_dict = l()

    for d in data:
        pdb = d['pdb_code__index']
        rep = d['representative']
        l3 = d['protein_conformation__protein__parent__family__name']
        l2 = d['protein_conformation__protein__parent__family__parent__name']
        l1 = d['protein_conformation__protein__parent__family__parent__parent__name']
        l0 = d['protein_conformation__protein__parent__family__parent__parent__parent__name']
        s3 = d['protein_conformation__protein__parent__family__slug']
        s2 = d['protein_conformation__protein__parent__family__parent__slug']
        s1 = d['protein_conformation__protein__parent__family__parent__parent__slug']
        s0 = d['protein_conformation__protein__parent__family__parent__parent__parent__slug']
        state = d['protein_conformation__state__slug']

        if rep:
            rep = 'R'
        else:
            rep = 'N'

        if state == 'active':
            state = 'A'
        else:
            state = 'I'

        if not data_dict[s0 + ',' + l0][s1 + ',' + l1][s2 + ',' + l2][s3 + ',' + l3]:
            data_dict[s0 + ',' + l0][s1 + ',' + l1][s2 + ',' + l2][s3 + ',' + l3] = []

        data_dict[s0 + ',' + l0][s1 + ',' + l1][s2 + ',' + l2][s3 + ',' + l3].append(pdb + ' (' + state + ')'  + '(' + rep + ')')

    return JsonResponse(data_dict)

@cache_page(60*60*24)
def PdbTableData(request):

    data = Structure.objects.filter(refined=False).select_related(
                "state",
                "pdb_code__web_resource",
                "protein_conformation__protein__species",
                "protein_conformation__protein__source",
                "protein_conformation__protein__family__parent__parent__parent",
                "publication__web_link__web_resource").prefetch_related(
                "stabilizing_agents", "construct__crystallization__crystal_method",
                "protein_conformation__protein__parent__endogenous_ligands__properities__ligand_type",
                "protein_conformation__site_protein_conformation__site")

    data_dict = OrderedDict()
    data_table = "<table class='display table' width='100%'><thead><tr><th></th><th></th><th></th><th></th><th></th><th></th><th></th><th>Date</th><th><input class='form-check-input check_all' type='checkbox' value='' onclick='check_all(this);'></th></thead><tbody>\n"
    for s in data:
        pdb_id = s.pdb_code.index
        r = {}
        r['protein'] = s.protein_conformation.protein.parent.entry_short()
        r['protein_long'] = s.protein_conformation.protein.parent.short()
        r['protein_family'] = s.protein_conformation.protein.parent.family.parent.short()
        r['class'] = s.protein_conformation.protein.parent.family.parent.parent.parent.short()
        r['species'] = s.protein_conformation.protein.species.common_name
        r['date'] = s.publication_date
        r['state'] = s.state.name
        r['representative'] = 'Yes' if s.representative else 'No'
        data_dict[pdb_id] = r
        data_table += "<tr><td>{}</td><td>{}</td><td><span>{}</span></td><td>{}</td><td>{}</td><td><span>{}</span></td><td>{}</td><td>{}</td><td data-sort='0'><input class='form-check-input pdb_selected' type='checkbox' value='' onclick='thisPDB(this);' long='{}'  id='{}'></tr>\n".format(r['class'],pdb_id,r['protein_long'],r['protein_family'],r['species'],r['state'],r['representative'],r['date'],r['protein_long'],pdb_id)
    data_table += "</tbody></table>"
    return HttpResponse(data_table)

def InteractionData(request):
    def gpcrdb_number_comparator(e1, e2):
            t1 = e1.split('x')
            t2 = e2.split('x')

            if e1 == e2:
                return 0

            if t1[0] == t2[0]:
                if t1[1] < t2[1]:
                    return -1
                else:
                    return 1

            if t1[0] < t2[0]:
                return -1
            else:
                return 1

    # PDB files
    try:
        pdbs = request.GET.getlist('pdbs[]')
    except IndexError:
        pdbs = []

    pdbs = [pdb.lower() for pdb in pdbs]

    # Segment filters
    try:
        segments = request.GET.getlist('segments[]')
    except IndexError:
        segments = []

    # Interaction types
    try:
        i_types = request.GET.getlist('interaction_types[]')
    except IndexError:
        i_types = []

    # Use generic numbers? Defaults to True.
    generic = True
    try:
        generic_string = request.GET.get('generic')
        if generic_string in ['false', 'False', 'FALSE', '0']:
            generic = False
    except IndexError:
        pass

    segment_filter_res1 = Q()
    segment_filter_res2 = Q()

    if segments:
        segment_filter_res1 |= Q(interacting_pair__res1__protein_segment__slug__in=segments)
        segment_filter_res2 |= Q(interacting_pair__res2__protein_segment__slug__in=segments)

    i_types_filter = Q()

    if i_types:
        i_types_filter |= Q(polymorphic_ctype__model__in=i_types)

    # Get the relevant interactions
    interactions = Interaction.objects.filter(
        interacting_pair__referenced_structure__protein_conformation__protein__entry_name__in=pdbs
    ).values(
        'interacting_pair__referenced_structure__protein_conformation__protein__entry_name',
        'interacting_pair__res1__amino_acid',
        'interacting_pair__res2__amino_acid',
        'interacting_pair__res1__sequence_number',
        'interacting_pair__res1__generic_number__label',
        'interacting_pair__res1__protein_segment__slug',
        'interacting_pair__res2__sequence_number',
        'interacting_pair__res2__generic_number__label',
        'interacting_pair__res2__protein_segment__slug',
        'polymorphic_ctype__model',
    ).filter(
        segment_filter_res1 & segment_filter_res2 & i_types_filter
    )

    # Initialize response dictionary
    data = {}
    data['interactions'] = {}
    data['pdbs'] = set()
    data['generic'] = generic
    data['segments'] = set()
    data['segment_map'] = {}
    # For Max schematics TODO -- make smarter.
    data['segment_map'] = {}
    data['aa_map'] = {}

    # Create a consensus sequence.

    excluded_segment = ['C-term','N-term']
    segments = ProteinSegment.objects.all().exclude(slug__in = excluded_segment)
    proteins =  Protein.objects.filter(protein__entry_name__in=pdbs).all()

    data['gn_map'] = OrderedDict()
    data['pos_map'] = OrderedDict()
    data['segment_map_full'] = OrderedDict()
    data['segment_map_full_gn'] = OrderedDict()
    data['generic_map_full'] = OrderedDict()

    if len(proteins)>1:
        a = Alignment()
        a.ignore_alternative_residue_numbering_schemes = True;
        a.load_proteins(proteins)
        a.load_segments(segments) #get all segments to make correct diagrams
        # build the alignment data matrix
        a.build_alignment()
        # calculate consensus sequence + amino acid and feature frequency
        a.calculate_statistics()
        consensus = a.full_consensus

        for aa in consensus:
            if 'x' in aa.family_generic_number:
                data['gn_map'][aa.family_generic_number] = aa.amino_acid
                data['pos_map'][aa.sequence_number] = aa.amino_acid
                data['segment_map_full_gn'][aa.family_generic_number] = aa.segment_slug
    else:
        rs = Residue.objects.filter(protein_conformation__protein=proteins[0]).prefetch_related('protein_segment','display_generic_number','generic_number')
        for r in rs:
            if (not generic):
                data['pos_map'][r.sequence_number] = r.amino_acid
                data['segment_map_full'][r.sequence_number] = r.protein_segment.slug
                if r.display_generic_number:
                    data['generic_map_full'][r.sequence_number] = r.short_display_generic_number()

    for i in interactions:
        pdb_name = i['interacting_pair__referenced_structure__protein_conformation__protein__entry_name']
        if not pdb_name in data['pdbs']:
            data['pdbs'].add(pdb_name)

    # Map from ordinary residue numbers to generic where available
    if (not generic):
        data['generic_map'] = {}

    # Dict to keep track of which residue numbers are in use
    number_dict = set()

    for i in interactions:
        pdb_name = i['interacting_pair__referenced_structure__protein_conformation__protein__entry_name']
        res1_seq = i['interacting_pair__res1__sequence_number']
        res2_seq = i['interacting_pair__res2__sequence_number']
        res1_gen = i['interacting_pair__res1__generic_number__label']
        res2_gen = i['interacting_pair__res2__generic_number__label']
        res1_seg = i['interacting_pair__res1__protein_segment__slug']
        res2_seg = i['interacting_pair__res2__protein_segment__slug']
        res1_aa = i['interacting_pair__res1__amino_acid']
        res2_aa = i['interacting_pair__res2__amino_acid']
        model = i['polymorphic_ctype__model']

        if generic and (not res1_gen or not res2_gen):
            continue

        # List PDB files that were found in dataset.
        data['pdbs'] |= {pdb_name}

        # Numbering convention
        res1 = res1_seq
        res2 = res2_seq

        if generic:
            res1 = res1_gen
            res2 = res2_gen

        if not generic and res1_gen:
            data['generic_map'][res1] = res1_gen

        if not generic and res2_gen:
            data['generic_map'][res2] = res2_gen

        # List which segments are available.
        data['segment_map'][res1] = res1_seg
        data['segment_map'][res2] = res2_seg
        data['segments'] |= {res1_seg} | {res2_seg}

        # Populate the AA map
        if pdb_name not in data['aa_map']:
            data['aa_map'][pdb_name] = {}

        data['aa_map'][pdb_name][res1] = res1_aa
        data['aa_map'][pdb_name][res2] = res2_aa

        number_dict |= {res1, res2}

        if res1 < res2:
            coord = str(res1) + ',' + str(res2)
        else:
            coord = str(res2) + ',' + str(res1)

        if coord not in data['interactions']:
            data['interactions'][coord] = {}

        if pdb_name not in data['interactions'][coord]:
            data['interactions'][coord][pdb_name] = []

        data['interactions'][coord][pdb_name].append(model)

    if (generic):
        data['sequence_numbers'] = sorted(number_dict, key=functools.cmp_to_key(gpcrdb_number_comparator))
    else:
        data['sequence_numbers'] = sorted(number_dict)

    data['segments'] = list(data['segments'])
    data['pdbs'] = list(data['pdbs'])

    return JsonResponse(data)

def ServePDB(request, pdbname):
    structure=Structure.objects.filter(pdb_code__index=pdbname.upper())
    if structure.exists():
        structure=structure.get()
    else:
        quit() #quit!

    if structure.pdb_data is None:
        quit()

    only_gns = list(structure.protein_conformation.residue_set.exclude(generic_number=None).values_list('protein_segment__slug','sequence_number','generic_number__label').all())
    only_gn = []
    gn_map = []
    segments = {}
    for gn in only_gns:
        only_gn.append(gn[1])
        gn_map.append(gn[2])
        if gn[0] not in segments:
            segments[gn[0]] = []
        segments[gn[0]].append(gn[1])
    data = {}
    data['pdb'] = structure.pdb_data.pdb
    data['only_gn'] = only_gn
    data['gn_map'] = gn_map
    data['segments'] = segments
    data['chain'] = structure.preferred_chain

    return JsonResponse(data)
