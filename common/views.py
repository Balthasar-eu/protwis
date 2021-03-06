﻿from django.http import HttpResponse
from django.shortcuts import render
from django.views.generic import TemplateView
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.db.models import Case, When
from django.core.cache import cache

from common.selection import SimpleSelection, Selection, SelectionItem
from common import definitions
from structure.models import Structure, StructureModel, StructureComplexModel
from protein.models import Protein, ProteinFamily, ProteinSegment, Species, ProteinSource, ProteinSet, ProteinGProtein, ProteinGProteinPair
from residue.models import ResidueGenericNumber, ResidueNumberingScheme, ResidueGenericNumberEquivalent, ResiduePositionSet
from interaction.forms import PDBform
from construct.tool import FileUploadForm

import inspect
from collections import OrderedDict
from io import BytesIO
import xlsxwriter, xlrd
import time
import json


class AbsTargetSelection(TemplateView):
    """An abstract class for the target selection page used in many apps. To use it in another app, create a class
    based view for that app that extends this class"""
    template_name = 'common/targetselection.html'

    type_of_selection = 'targets'
    selection_only_receptors = False
    step = 1
    number_of_steps = 2
    title = 'SELECT TARGETS'
    description = 'Select targets by searching or browsing in the middle column. You can select entire target' \
        + ' families or individual targets.\n\nYou can also enter the list of UNIPROT names of the targets (one per line) and click "Add targets" button to add those.\n\nSelected targets will appear in the right column, where you can edit' \
        + ' the list.\n\nOnce you have selected all your targets, click the green button.'
    documentation_url = settings.DOCUMENTATION_URL
    docs = False
    filters = True
    target_input = True
    default_species = 'Human'
    default_slug = '000'
    numbering_schemes = False
    search = True
    family_tree = True
    redirect_on_select = False
    filter_gprotein = False
    selection_heading = False
    buttons = {
        'continue': {
            'label': 'Continue to next step',
            'url': '#',
            'color': 'success',
        },
    }
    # OrderedDict to preserve the order of the boxes
    selection_boxes = OrderedDict([
        ('reference', False),
        ('targets', True),
        ('segments', False),
    ])

    # proteins and families
    #try - except block prevents manage.py from crashing - circular dependencies between protein - common
    try:
        if ProteinFamily.objects.filter(slug=default_slug).exists():
            ppf = ProteinFamily.objects.get(slug=default_slug)
            pfs = ProteinFamily.objects.filter(parent=ppf.id)
            ps = Protein.objects.filter(family=ppf)
            psets = ProteinSet.objects.all().prefetch_related('proteins')
            tree_indent_level = []
            action = 'expand'
            # remove the parent family (for all other families than the root of the tree, the parent should be shown)
            del ppf
    except Exception as e:
        pass

    # species
    sps = Species.objects.all()

    # g proteins
    gprots = ProteinGProtein.objects.all()

    # numbering schemes
    gns = ResidueNumberingScheme.objects.exclude(slug=settings.DEFAULT_NUMBERING_SCHEME).exclude(slug='cgn')

    def get_context_data(self, **kwargs):
        """get context from parent class (really only relevant for children of this class, as TemplateView does
        not have any context variables)"""
        context = super().get_context_data(**kwargs)

        # get selection from session and add to context
        # get simple selection from session
        simple_selection = self.request.session.get('selection', False)

        # create full selection and import simple selection (if it exists)
        selection = Selection()

        # on the first page of a workflow, clear the selection (or dont' import from the session)
        if self.step is not 1:
            if simple_selection:
                selection.importer(simple_selection)

        # default species selection
        if self.default_species:
            sp = Species.objects.get(common_name=self.default_species)
            o = SelectionItem('species', sp)
            selection.species = [o]

        # update session
        simple_selection = selection.exporter()
        self.request.session['selection'] = simple_selection

        context['selection'] = {}
        for selection_box, include in self.selection_boxes.items():
            if include:
                context['selection'][selection_box] = selection.dict(selection_box)['selection'][selection_box]

        if self.filters:
            context['selection']['species'] = selection.species
            context['selection']['annotation'] = selection.annotation
            context['selection']['g_proteins'] = selection.g_proteins
            context['selection']['pref_g_proteins'] = selection.pref_g_proteins

        # get attributes of this class and add them to the context
        attributes = inspect.getmembers(self, lambda a:not(inspect.isroutine(a)))
        for a in attributes:
            if not(a[0].startswith('__') and a[0].endswith('__')):
                context[a[0]] = a[1]
        return context

class AbsReferenceSelection(AbsTargetSelection):
    type_of_selection = 'reference'
    step = 1
    number_of_steps = 3
    title = 'SELECT A REFERENCE TARGET'
    description = 'Select a reference target by searching or browsing in the right column.\n\nThe reference will be compared to the targets you select later in the workflow.\n\nOnce you have selected your reference target, you will be redirected to the next step.'
    redirect_on_select = True
    selection_boxes = OrderedDict([
        ('reference', True),
        ('targets', False),
        ('segments', False),
    ])
    psets = [] # protein sets not applicable for this selection

class AbsBrowseSelection(AbsTargetSelection):
    type_of_selection = 'browse'
    step = 1
    number_of_steps = 1
    title = 'SELECT A TARGET OR FAMILY'
    description = 'Select a target or family by searching or browsing in the right column.'
    psets = [] # protein sets not applicable for this selection

class AbsSegmentSelection(TemplateView):
    """An abstract class for the segment selection page used in many apps. To use it in another app, create a class
    based view for that app that extends this class"""
    template_name = 'common/segmentselection.html'

    step = 2
    number_of_steps = 2
    title = 'SELECT SEQUENCE SEGMENTS'
    description = 'Select sequence segments in the middle column. You can expand helices and select individual' \
        + ' residues by clicking on the down arrows next to each helix.\n\nSelected segments will appear in the' \
        + ' right column, where you can edit the list.\n\nOnce you have selected all your segments, click the green' \
        + ' button.'
    documentation_url = settings.DOCUMENTATION_URL
    docs = False
    segment_list = True
    structure_upload = False
    upload_form = PDBform()
    position_type = 'residue'
    buttons = {
        'continue': {
            'label': 'Show alignment',
            'url': '/alignment/render',
            'color': 'success',
        },
    }
    # OrderedDict to preserve the order of the boxes
    selection_boxes = OrderedDict([
        ('reference', False),
        ('targets', True),
        ('segments', True),
    ])

    try:
        rsets = ResiduePositionSet.objects.filter(protein_group='gpcr').prefetch_related('residue_position').order_by('set_type','name')
    except Exception as e:
        pass

    ss = ProteinSegment.objects.filter(partial=False, proteinfamily='GPCR').prefetch_related('generic_numbers')
    ss_cats = ss.values_list('category').order_by('category').distinct('category')
    action = 'expand'

    amino_acid_groups = definitions.AMINO_ACID_GROUPS
    amino_acid_group_names = definitions.AMINO_ACID_GROUP_NAMES

    def get_context_data(self, **kwargs):
        """get context from parent class (really only relevant for child classes of this class, as TemplateView does
        not have any context variables)"""
        context = super().get_context_data(**kwargs)

        # get selection from session and add to context
        # get simple selection from session
        simple_selection = self.request.session.get('selection', False)

        # create full selection and import simple selection (if it exists)
        selection = Selection()
        if simple_selection:
            selection.importer(simple_selection)

        # context['selection'] = selection
        context['selection'] = {}
        context['selection']['site_residue_groups'] = selection.site_residue_groups
        context['selection']['active_site_residue_group'] = selection.active_site_residue_group
        for selection_box, include in self.selection_boxes.items():
            if include:
                context['selection'][selection_box] = selection.dict(selection_box)['selection'][selection_box]

        # get attributes of this class and add them to the context
        attributes = inspect.getmembers(self, lambda a:not(inspect.isroutine(a)))
        for a in attributes:
            if not(a[0].startswith('__') and a[0].endswith('__')):
                context[a[0]] = a[1]
        return context

class AbsMiscSelection(TemplateView):
    """An abstract class for selection pages of other types than target- and segmentselection"""
    template_name = 'common/miscselection.html'
    step = 3
    number_of_steps = 3
    title = ''
    description = ''
    documentation_url = settings.DOCUMENTATION_URL
    docs = False
    buttons = {}
    tree_settings = False
    blast_input = False

    # OrderedDict to preserve the order of the boxes
    selection_boxes = OrderedDict([
        ('targets', True),
        ('segments', True),
    ])
    def get_context_data(self, **kwargs):
        """get context from parent class (really only relevant for child classes of this class, as TemplateView does
        not have any context variables)"""
        context = super().get_context_data(**kwargs)

        # get selection from session and add to context
        # get simple selection from session
        simple_selection = self.request.session.get('selection', False)

        # create full selection and import simple selection (if it exists)
        selection = Selection()

        # on the first page of a workflow, clear the selection (or dont' import from the session)
        if self.step is not 1:
            if simple_selection:
                selection.importer(simple_selection)

        context['selection'] = {}
        context['selection']['tree_settings'] = selection.tree_settings

        for selection_box, include in self.selection_boxes.items():
            if include:
                context['selection'][selection_box] = selection.dict(selection_box)['selection'][selection_box]

        # get attributes of this class and add them to the context
        attributes = inspect.getmembers(self, lambda a:not(inspect.isroutine(a)))
        for a in attributes:
            if not(a[0].startswith('__') and a[0].endswith('__')):
                context[a[0]] = a[1]
        return context

def AddToSelection(request):
    """Receives a selection request, adds the selected item to session, and returns the updated selection"""
    selection_type = request.GET['selection_type']
    selection_subtype = request.GET['selection_subtype']
    selection_id = request.GET['selection_id']
    # get simple selection from session
    simple_selection = request.session.get('selection', False)

    # create full selection and import simple selection (if it exists)
    selection = Selection()
    if simple_selection:
        selection.importer(simple_selection)

    # process selected object
    o = []
    if selection_type == 'reference' or selection_type == 'targets':
        if selection_subtype == 'protein':
            o.append(Protein.objects.get(pk=selection_id))
        if selection_subtype == 'protein_entry':
            o.append(Protein.objects.get(entry_name=selection_id))
            print("Added {}".format(Protein.objects.get(entry_name=selection_id).name))

        elif selection_subtype == 'protein_set':
            selection_subtype = 'protein'
            pset = ProteinSet.objects.get(pk=selection_id)
            for protein in pset.proteins.all():
                o.append(protein)

        elif selection_subtype == 'family':
            o.append(ProteinFamily.objects.get(pk=selection_id))

        elif selection_subtype == 'set':
            o.append(ProteinSet.objects.get(pk=selection_id))

        elif selection_subtype == 'structure':
            if 'refined' in selection_id:
                sel1, sel2 = selection_id.split('_')
                o.append(Structure.objects.get(pdb_code__index=sel1.upper()+'_refined'))
            else:
                o.append(Structure.objects.get(pdb_code__index=selection_id.upper()))

        elif selection_subtype == 'structure_many':
            selection_subtype = 'structure'
            for pdb_code in selection_id.split(","):
                if 'refined' in pdb_code:
                    sel1, sel2 = pdb_code.split('_')
                    o.append(Structure.objects.get(pdb_code__index=sel1.upper()+'_refined'))
                else:
                    o.append(Structure.objects.get(pdb_code__index=pdb_code.upper()))

        elif selection_subtype == 'structure_model':
            o.append(StructureModel.objects.defer('pdb').filter(protein__entry_name=selection_id)[0])

        elif selection_subtype == 'structure_complex_receptor':
            o.append(StructureComplexModel.objects.defer('pdb').filter(receptor_protein__entry_name=selection_id)[0])

        elif selection_subtype == 'structure_complex_signprot':
            o.append(StructureComplexModel.objects.defer('pdb').filter(sign_protein__entry_name=selection_id)[0])

        elif selection_subtype == 'structure_models_many':
            selection_subtype = 'structure_model'
            for model in selection_id.split(","):
                state = model.split('_')[-1]
                entry_name = '_'.join(model.split('_')[:-1])
                o.append(StructureModel.objects.defer('pdb').get(protein__entry_name=entry_name, state__name=state))


    elif selection_type == 'segments':
        if selection_subtype == 'residue':
            o.append(ResidueGenericNumberEquivalent.objects.get(pk=selection_id))
        elif selection_subtype == 'residue_position_set':
            selection_subtype = 'residue'
            rset = ResiduePositionSet.objects.get(pk=selection_id)
            for residue in rset.residue_position.all():
                o.append(residue)
        elif selection_subtype == 'site_residue': # used in site search
            o.append(ResidueGenericNumberEquivalent.objects.get(pk=selection_id))

        else:
            o.append(ProteinSegment.objects.get(pk=selection_id))

    for obj in o:
        # add the selected item to the selection
        selection_object = SelectionItem(selection_subtype, obj)
        selection.add(selection_type, selection_subtype, selection_object)

    # export simple selection that can be serialized
    simple_selection = selection.exporter()
    # add simple selection to session
    request.session['selection'] = simple_selection

    # context
    context = selection.dict(selection_type)

    # template to load
    if selection_subtype == 'site_residue':
        template = 'common/selection_lists_sitesearch.html'
        amino_acid_groups = {
            'amino_acid_groups': definitions.AMINO_ACID_GROUPS,
            'amino_acid_group_names': definitions.AMINO_ACID_GROUP_NAMES }
        context.update(amino_acid_groups)
    else:
        template = 'common/selection_lists.html'

    # amino acid groups
    return render(request, template, context)

def RemoveFromSelection(request):
    """Removes one selected item from the session"""
    selection_type = request.GET['selection_type']
    selection_subtype = request.GET['selection_subtype']
    selection_id = request.GET['selection_id']

    # get simple selection from session
    simple_selection = request.session.get('selection', False)

    # create full selection and import simple selection (if it exists)
    selection = Selection()
    if simple_selection:
        selection.importer(simple_selection)

    # remove the selected item to the selection
    selection.remove(selection_type, selection_subtype, selection_id)

    # export simple selection that can be serialized
    simple_selection = selection.exporter()

    # add simple selection to session
    request.session['selection'] = simple_selection

    # context
    context = selection.dict(selection_type)

    # template to load
    if selection_subtype == 'site_residue':
        template = 'common/selection_lists_sitesearch.html'
        amino_acid_groups = {
            'amino_acid_groups': definitions.AMINO_ACID_GROUPS,
            'amino_acid_group_names': definitions.AMINO_ACID_GROUP_NAMES }
        context.update(amino_acid_groups)
    else:
        template = 'common/selection_lists.html'

    return render(request, template, context)

def ClearSelection(request):
    """Clears all selected items of the selected type from the session"""
    selection_type = request.GET['selection_type']

    # get simple selection from session
    simple_selection = request.session.get('selection', False)

    # create full selection and import simple selection (if it exists)
    selection = Selection()
    if simple_selection:
        selection.importer(simple_selection)

    # remove the selected item to the selection
    selection.clear(selection_type)

    # export simple selection that can be serialized
    simple_selection = selection.exporter()

    # add simple selection to session
    request.session['selection'] = simple_selection

    return render(request, 'common/selection_lists.html', selection.dict(selection_type))

def SelectRange(request):
    """Adds generic numbers within the given range"""

    selection_type = request.GET['selection_type']
    selection_subtype = request.GET['selection_subtype']
    range_start = request.GET['range_start']
    range_end = request.GET['range_end']

    # get simple selection from session
    simple_selection = request.session.get('selection', False)

    # create full selection and import simple selection (if it exists)
    selection = Selection()
    if simple_selection:
        selection.importer(simple_selection)

    # process selected object
    o = []
    if selection_type == 'segments' and selection_subtype == 'residue':
        residue_nums = ResidueGenericNumberEquivalent.objects.all()
        for resn in residue_nums:
            if range_start < float(resn.label.replace('x','.')) < range_end:
                o.append(resn)

def SelectFullSequence(request):
    """Adds all segments to the selection"""
    selection_type = request.GET['selection_type']

    # get simple selection from session
    simple_selection = request.session.get('selection', False)

    # create full selection and import simple selection (if it exists)
    selection = Selection()
    if simple_selection:
        selection.importer(simple_selection)

    # get all segments
    if "protein_type" in request.GET:
        if request.GET['protein_type'] == 'gprotein':
            segmentlist = definitions.G_PROTEIN_SEGMENTS
        else:
            segmentlist = definitions.ARRESTIN_SEGMENTS

        preserved = Case(*[When(slug=pk, then=pos) for pos, pk in enumerate(segmentlist['Full'])])
        segments = ProteinSegment.objects.filter(slug__in = segmentlist['Full'], partial=False).order_by(preserved)


    else:
        segments = ProteinSegment.objects.filter(partial=False, proteinfamily='GPCR')


    for segment in segments:
        selection_object = SelectionItem(segment.category, segment)
        # add the selected item to the selection
        selection.add(selection_type, segment.category, selection_object)

    # export simple selection that can be serialized
    simple_selection = selection.exporter()

    # add simple selection to session
    request.session['selection'] = simple_selection

    return render(request, 'common/selection_lists.html', selection.dict(selection_type))

def SetTreeSelection(request):
    """Adds all alignable segments to the selection"""
    option_no = request.GET['option_no']
    option_id = request.GET['option_id']
    # get simple selection from session
    simple_selection = request.session.get('selection', False)
    # create full selection and import simple selection (if it exists)
    selection = Selection()
    if simple_selection:
        selection.importer(simple_selection)
    selection.tree_settings[int(option_no)]=option_id
    simple_selection = selection.exporter()
    # add simple selection to session
    request.session['selection'] = simple_selection
    return render(request, 'common/tree_options.html', selection.dict('tree_settings'))

def SelectAlignableSegments(request):
    """Adds all alignable segments to the selection"""
    selection_type = request.GET['selection_type']

    # get simple selection from session
    simple_selection = request.session.get('selection', False)

    # create full selection and import simple selection (if it exists)
    selection = Selection()
    if simple_selection:
        selection.importer(simple_selection)

    # get specific segments
    if "protein_type" in request.GET:
        if request.GET['protein_type'] == 'gprotein':
            segmentlist = definitions.G_PROTEIN_SEGMENTS
        else:
            segmentlist = definitions.ARRESTIN_SEGMENTS

        preserved = Case(*[When(slug=pk, then=pos) for pos, pk in enumerate(segmentlist['Structured'])])
        segments = ProteinSegment.objects.filter(slug__in = segmentlist['Structured'], partial=False).order_by(preserved)
    else:
        segments = ProteinSegment.objects.filter(partial=False, slug__startswith='TM')

    for segment in segments:
        selection_object = SelectionItem(segment.category, segment)
        # add the selected item to the selection
        selection.add(selection_type, segment.category, selection_object)

    # export simple selection that can be serialized
    simple_selection = selection.exporter()

    # add simple selection to session
    request.session['selection'] = simple_selection

    return render(request, 'common/selection_lists.html', selection.dict(selection_type))

def SelectAlignableResidues(request):
    """Adds all alignable residues to the selection"""
    selection_type = request.GET['selection_type']

    # get simple selection from session
    simple_selection = request.session.get('selection', False)

    # create full selection and import simple selection (if it exists)
    selection = Selection()
    if simple_selection:
        selection.importer(simple_selection)

    if "protein_type" in request.GET:
        if request.GET['protein_type'] == 'gprotein':
            segmentlist = definitions.G_PROTEIN_SEGMENTS
        else:
            segmentlist = definitions.ARRESTIN_SEGMENTS

        preserved = Case(*[When(slug=pk, then=pos) for pos, pk in enumerate(segmentlist['Structured'])])
        segments = ProteinSegment.objects.filter(slug__in = segmentlist['Structured'], partial=False).order_by(preserved)
    else:
        segments = ProteinSegment.objects.filter(proteinfamily='GPCR').order_by('pk')

    numbering_scheme_slug = 'false'

    # find the relevant numbering scheme (based on target selection)
    cgn = False
    if numbering_scheme_slug == 'cgn':
        cgn = True
    elif numbering_scheme_slug == 'false':
        if simple_selection.reference:
            first_item = simple_selection.reference[0]
        else:
            first_item = simple_selection.targets[0]
        if first_item.type == 'family':
            proteins = Protein.objects.filter(family__slug__startswith=first_item.item.slug)
            numbering_scheme = proteins[0].residue_numbering_scheme
        elif first_item.type == 'protein':
            numbering_scheme = first_item.item.residue_numbering_scheme
    else:
        numbering_scheme = ResidueNumberingScheme.objects.get(slug=numbering_scheme_slug)

    for segment in segments:
        if segment.fully_aligned:
            selection_object = SelectionItem(segment.category, segment)
            # add the selected item to the selection
            selection.add(selection_type, segment.category, selection_object)
        else:
            #if not fully aligned

            if ResidueGenericNumberEquivalent.objects.filter(
            default_generic_number__protein_segment=segment,
            scheme=numbering_scheme).exists():
                segment.only_aligned_residues = True
                selection_object = SelectionItem(segment.category, segment, properties={'only_aligned_residues':True})
                selection.add(selection_type, segment.category, selection_object)


    simple_selection = selection.exporter()
    # add simple selection to session
    request.session['selection'] = simple_selection

    return render(request, 'common/selection_lists.html', selection.dict('segments'))

def ToggleFamilyTreeNode(request):
    """Opens/closes a node in the family selection tree"""
    action = request.GET['action']
    type_of_selection = request.GET['type_of_selection']

    node_id = request.GET['node_id']
    parent_tree_indent_level = int(request.GET['tree_indent_level'])
    tree_indent_level = []
    for i in range(parent_tree_indent_level+1):
        tree_indent_level.append(0)
    parent_tree_indent_level = tree_indent_level[:]
    del parent_tree_indent_level[-1]

    # session
    simple_selection = request.session.get('selection')
    selection = Selection()
    if simple_selection:
        selection.importer(simple_selection)

    ppf = ProteinFamily.objects.get(pk=node_id)
    if action == 'expand':
        pfs = ProteinFamily.objects.filter(parent=node_id)

        # species filter
        species_list = []
        for species in selection.species:
            species_list.append(species.item)

        # annotation filter
        protein_source_list = []
        for protein_source in selection.annotation:
            protein_source_list.append(protein_source.item)

        # preferred g proteins filter
        pref_g_proteins_list = []
        for g_protein in selection.pref_g_proteins:
            pref_g_proteins_list.append(g_protein.item)


        # g proteins filter
        g_proteins_list = []
        for g_protein in selection.g_proteins:
            g_proteins_list.append(g_protein.item)

        if species_list:
            ps = Protein.objects.order_by('id').filter(family=ppf,
                species__in=(species_list),
                source__in=(protein_source_list)).order_by('source_id', 'id')
        else:
            ps = Protein.objects.order_by('id').filter(family=ppf,
                source__in=(protein_source_list)).order_by('source_id', 'id')
        if pref_g_proteins_list:
            proteins = [x.protein_id for x in ProteinGProteinPair.objects.filter(g_protein__in=g_proteins_list, transduction='primary')]
            ps = Protein.objects.order_by('id').filter(pk__in=proteins).filter(pk__in=ps)

        if g_proteins_list:
            proteins = [x.protein_id for x in ProteinGProteinPair.objects.filter(g_protein__in=g_proteins_list)]
            ps = Protein.objects.order_by('id').filter(pk__in=proteins).filter(pk__in=ps)

        action = 'collapse'
    else:
        pfs = ps = {}
        action = 'expand'

    return render(request, 'common/selection_tree.html', {
        'action': action,
        'type_of_selection': type_of_selection,
        'ppf': ppf,
        'pfs': pfs,
        'ps': ps,
        'parent_tree_indent_level': parent_tree_indent_level,
        'tree_indent_level': tree_indent_level,
    })

def SelectionAnnotation(request):
    """Updates the selected level of annotation"""
    protein_source = request.GET['protein_source']

    if protein_source == 'All':
        pss = ProteinSource.objects.all()
    else:
        pss = ProteinSource.objects.filter(name=protein_source)

    # get simple selection from session
    simple_selection = request.session.get('selection', False)

    # create full selection and import simple selection (if it exists)
    selection = Selection()
    if simple_selection:
        selection.importer(simple_selection)

    # reset the annotation selection
    selection.clear('annotation')

    # add the selected items to the selection
    for ps in pss:
        selection_object = SelectionItem('annotation', ps)
        selection.add('annotation', 'annotation', selection_object)

    # export simple selection that can be serialized
    simple_selection = selection.exporter()

    # add simple selection to session
    request.session['selection'] = simple_selection
    return render(request, 'common/selection_filters_annotation.html', selection.dict('annotation'))

def SelectionSpeciesPredefined(request):
    """Updates the selected species to predefined sets (Human and all)"""
    species = request.GET['species']

    # get simple selection from session
    simple_selection = request.session.get('selection', False)

    # create full selection and import simple selection (if it exists)
    selection = Selection()
    if simple_selection:
        selection.importer(simple_selection)

    all_sps = Species.objects.all()
    sps = False
    if species == 'All':
        sps = []
    if species != 'All' and species:
        sps = Species.objects.filter(common_name=species)

    if sps != False:
        # reset the species selection
        selection.clear('species')

        # add the selected items to the selection
        for sp in sps:
            selection_object = SelectionItem('species', sp)
            selection.add('species', 'species', selection_object)

    # export simple selection that can be serialized
    simple_selection = selection.exporter()

    # add simple selection to session
    request.session['selection'] = simple_selection

    # add all species objects to context (for comparison to selected species)
    context = selection.dict('species')
    context['sps'] = all_sps

    return render(request, 'common/selection_filters_species.html', context)

def SelectionSpeciesToggle(request):
    """Updates the selected species arbitrary selections"""
    species_id = request.GET['species_id']

    all_sps = Species.objects.all()
    sps = Species.objects.filter(pk=species_id)

    # get simple selection from session
    simple_selection = request.session.get('selection', False)

    # create full selection and import simple selection (if it exists)
    selection = Selection()
    if simple_selection:
        selection.importer(simple_selection)

    # add the selected items to the selection
    for sp in sps:
        exists = selection.remove('species', 'species', species_id)
        if not exists:
            selection_object = SelectionItem('species', sp)
            selection.add('species', 'species', selection_object)

    # export simple selection that can be serialized
    simple_selection = selection.exporter()

    # add simple selection to session
    request.session['selection'] = simple_selection

    # add all species objects to context (for comparison to selected species)
    context = selection.dict('species')
    context['sps'] = Species.objects.all()

    return render(request, 'common/selection_filters_species_selector.html', context)

def SelectionGproteinPredefined(request):
    """Updates the selected g proteins to predefined sets (Gi/Go and all)"""
    g_protein = request.GET['g_protein']
    preferred = request.GET['preferred']

    # get simple selection from session
    simple_selection = request.session.get('selection', False)

    # create full selection and import simple selection (if it exists)
    selection = Selection()
    if simple_selection:
        selection.importer(simple_selection)

    all_gprots = ProteinGProtein.objects.all()
    gprots = False
    if g_protein == 'All':
        gprots = []
    if g_protein != 'All' and g_protein:
        gprots = ProteinGProtein.objects.filter(name=g_protein)

    if gprots != False:
        # reset the g proteins selection
        if preferred == 'true':
            selection.clear('pref_g_proteins')
        else:
            selection.clear('g_proteins')

        # add the selected items to the selection
        for gprot in gprots:
            selection_object = SelectionItem('g_protein', gprot)
            if preferred == 'true':
                selection.add('pref_g_proteins', 'g_protein', selection_object)
            else:
                selection.add('g_proteins', 'g_protein', selection_object)

    # export simple selection that can be serialized
    simple_selection = selection.exporter()

    # add simple selection to session
    request.session['selection'] = simple_selection

    # add all species objects to context (for comparison to selected species)
    if preferred == 'true':
        context = selection.dict('pref_g_proteins')
    else:
        context = selection.dict('g_proteins')
    context['gprots'] = ProteinGProtein.objects.all()

    if preferred == 'true':
        return render(request, 'common/selection_filters_pref_gproteins.html', context)
    else:
        return render(request, 'common/selection_filters_gproteins.html', context)

def SelectionGproteinToggle(request):
    """Updates the selected g proteins arbitrary selections"""
    g_protein_id = request.GET['g_protein_id']
    preferred = request.GET['preferred']

    all_gprots = ProteinGProtein.objects.all()
    gprots = ProteinGProtein.objects.filter(pk=g_protein_id)
    print("'{}'".format(ProteinGProtein.objects.get(pk=g_protein_id).name))

    # get simple selection from session
    simple_selection = request.session.get('selection', False)

    # create full selection and import simple selection (if it exists)
    selection = Selection()
    if simple_selection:
        selection.importer(simple_selection)

    # add the selected items to the selection
    for gprot in gprots:
        if preferred == 'true':
            exists = selection.remove('pref_g_proteins', 'g_protein', g_protein_id)
        else:
            exists = selection.remove('g_proteins', 'g_protein', g_protein_id)
        if not exists:
            selection_object = SelectionItem('g_protein', gprot)
            if preferred == 'true':
                selection.add('pref_g_proteins', 'g_protein', selection_object)
            else:
                selection.add('g_proteins', 'g_protein', selection_object)

    # export simple selection that can be serialized
    simple_selection = selection.exporter()

    # add simple selection to session
    request.session['selection'] = simple_selection

    # add all species objects to context (for comparison to selected species)
    if preferred == 'true':
        context = selection.dict('pref_g_proteins')
    else:
        context = selection.dict('g_proteins')

    context['gprots'] = ProteinGProtein.objects.all()

    if preferred == 'true':
        # print(request.session['selection'])
        return render(request, 'common/selection_filters_pref_gproteins_selector.html', context)
    else:
        return render(request, 'common/selection_filters_gproteins_selector.html', context)

def ExpandSegment(request):
    """Expands a segment to show it's generic numbers"""
    segment_id = request.GET['segment_id']
    position_type = request.GET['position_type']
    numbering_scheme_slug = str(request.GET['numbering_scheme'])

    # get simple selection from session
    simple_selection = request.session.get('selection', False)

    # find the relevant numbering scheme (based on target selection)
    cgn = False
    if numbering_scheme_slug == 'cgn':
        cgn = True
    elif numbering_scheme_slug == 'false':
        if simple_selection.reference:
            first_item = simple_selection.reference[0]
        else:
            first_item = simple_selection.targets[0]
        if first_item.type == 'family':
            proteins = Protein.objects.filter(family__slug__startswith=first_item.item.slug)
            numbering_scheme = proteins[0].residue_numbering_scheme
        elif first_item.type == 'protein':
            numbering_scheme = first_item.item.residue_numbering_scheme
    else:
        numbering_scheme = ResidueNumberingScheme.objects.get(slug=numbering_scheme_slug)

    if cgn ==True:
        # fetch the generic numbers for CGN differently
        context = {}
        context['generic_numbers'] = ResidueGenericNumberEquivalent.objects.filter(
            default_generic_number__protein_segment__id=segment_id,
            scheme__slug='cgn').order_by('label')
        context['position_type'] = position_type
        context['scheme'] = ResidueNumberingScheme.objects.filter(slug='cgn')
        context['schemes'] = ResidueNumberingScheme.objects.filter(slug='cgn')
        context['segment_id'] = segment_id
    else:
        # fetch the generic numbers
        context = {}
        context['generic_numbers'] = ResidueGenericNumberEquivalent.objects.filter(
            default_generic_number__protein_segment__id=segment_id,
            scheme=numbering_scheme).order_by('label')
        context['position_type'] = position_type
        context['scheme'] = numbering_scheme
        context['schemes'] = ResidueNumberingScheme.objects.filter(parent__isnull=False)
        context['segment_id'] = segment_id
        print(context['scheme'], context['schemes'])

    return render(request, 'common/segment_generic_numbers.html', context)

def SelectionSchemesPredefined(request):
    """Updates the selected numbering_schemes to predefined sets (GPCRdb and All)"""
    numbering_schemes = request.GET['numbering_schemes']

    # get simple selection from session
    simple_selection = request.session.get('selection', False)

    # create full selection and import simple selection (if it exists)
    selection = Selection()
    if simple_selection:
        selection.importer(simple_selection)

    all_gns = ResidueNumberingScheme.objects.exclude(slug=settings.DEFAULT_NUMBERING_SCHEME).exclude(slug='cgn')
    gns = False
    if numbering_schemes == 'All':
        print(len(selection.numbering_schemes), all_gns.count())
        if len(selection.numbering_schemes) == all_gns.count():
            gns = []
        else:
            gns = all_gns

    # reset the species selection
    selection.clear('numbering_schemes')

    # add the selected items to the selection
    for gn in gns:
        selection_object = SelectionItem('numbering_schemes', gn)
        selection.add('numbering_schemes', 'numbering_schemes', selection_object)

    # export simple selection that can be serialized
    simple_selection = selection.exporter()

    # add simple selection to session
    request.session['selection'] = simple_selection

    # add all species objects to context (for comparison to selected species)
    context = selection.dict('numbering_schemes')
    context['gns'] = all_gns

    return render(request, 'common/selection_filters_numbering_schemes.html', context)

def SelectionSchemesToggle(request):
    """Updates the selected numbering schemes arbitrary selections"""
    numbering_scheme_id = request.GET['numbering_scheme_id']
    gns = ResidueNumberingScheme.objects.filter(pk=numbering_scheme_id).exclude(slug='cgn')

    # get simple selection from session
    simple_selection = request.session.get('selection', False)

    # create full selection and import simple selection (if it exists)
    selection = Selection()
    if simple_selection:
        selection.importer(simple_selection)

    # add the selected items to the selection
    for gn in gns:
        exists = selection.remove('numbering_schemes', 'numbering_schemes', numbering_scheme_id)
        if not exists:
            selection_object = SelectionItem('numbering_schemes', gn)
            selection.add('numbering_schemes', 'numbering_schemes', selection_object)

    # export simple selection that can be serialized
    simple_selection = selection.exporter()

    # add simple selection to session
    request.session['selection'] = simple_selection

    # add all species objects to context (for comparison to selected species)
    context = selection.dict('numbering_schemes')
    context['gns'] = ResidueNumberingScheme.objects.exclude(slug=settings.DEFAULT_NUMBERING_SCHEME).exclude(slug='cgn')

    return render(request, 'common/selection_filters_numbering_schemes.html', context)

def UpdateSiteResidueFeatures(request):
    """Updates the selected features of a site residue"""
    selection_type = request.GET['selection_type']
    selection_subtype = request.GET['selection_subtype']
    selection_id = request.GET['selection_id']

    o = []
    if selection_type == 'reference' or selection_type == 'targets':
        if selection_subtype == 'protein':
            o.append(Protein.objects.get(pk=selection_id))
        elif selection_subtype == 'protein_set':
            selection_subtype = 'protein'
            pset = ProteinSet.objects.get(pk=selection_id)
            for protein in pset.proteins.all():
                o.append(protein)
        elif selection_subtype == 'family':
            o.append(ProteinFamily.objects.get(pk=selection_id))
        elif selection_subtype == 'set':
            o.append(ProteinSet.objects.get(pk=selection_id))
        elif selection_subtype == 'structure':
            o.append(Protein.objects.get(entry_name=selection_id.lower()))
    elif selection_type == 'segments':
        if selection_subtype == 'residue':
            o.append(ResidueGenericNumberEquivalent.objects.get(pk=selection_id))
        elif selection_subtype == 'residue_with_properties': # used in site search
            o.append(ResidueGenericNumberEquivalent.objects.get(pk=selection_id))
        else:
            o.append(ProteinSegment.objects.get(pk=selection_id))

    # get simple selection from session
    simple_selection = request.session.get('selection', False)

    # create full selection and import simple selection (if it exists)
    selection = Selection()
    if simple_selection:
        selection.importer(simple_selection)

    for obj in o:
        # add the selected item to the selection
        selection_object = SelectionItem(selection_subtype, obj)
        selection.add(selection_type, selection_subtype, selection_object)

    # export simple selection that can be serialized
    simple_selection = selection.exporter()

    # add simple selection to session
    request.session['selection'] = simple_selection

    return render(request, 'common/selection_lists.html', selection.dict(selection_type))

def SelectResidueFeature(request):
    """Receives a selection request, add a feature selection to an item, and returns the updated selection"""
    selection_type = request.GET['selection_type']
    selection_subtype = request.GET['selection_subtype']
    selection_id = int(request.GET['selection_id'])
    feature = request.GET['feature']

    # get simple selection from session
    simple_selection = request.session.get('selection', False)

    # create full selection and import simple selection (if it exists)
    selection = Selection()
    if simple_selection:
        selection.importer(simple_selection)

    # process selected object
    if selection_type == 'segments' and selection_subtype == 'site_residue':
        for obj in selection.segments:
            if int(obj.item.id) == selection_id:
                obj.properties['feature'] = feature
                obj.properties['amino_acids'] = ','.join(definitions.AMINO_ACID_GROUPS[feature])
                break

    # export simple selection that can be serialized
    simple_selection = selection.exporter()

    # add simple selection to session
    request.session['selection'] = simple_selection

    # context
    context = selection.dict(selection_type)

    # amino acid groups
    amino_acid_groups = {
        'amino_acid_groups': definitions.AMINO_ACID_GROUPS,
        'amino_acid_group_names': definitions.AMINO_ACID_GROUP_NAMES }
    context.update(amino_acid_groups)

    # template to load
    template = 'common/selection_lists_sitesearch.html'

    return render(request, template, context)

def AddResidueGroup(request):
    """Receives a selection request, creates a new residue group, and returns the updated selection"""
    selection_type = request.GET['selection_type']

    # get simple selection from session
    simple_selection = request.session.get('selection', False)

    # create full selection and import simple selection (if it exists)
    selection = Selection()
    if simple_selection:
        selection.importer(simple_selection)

    # add a group

    selection.site_residue_groups.append([])

    # make the new group active
    selection.active_site_residue_group = len(selection.site_residue_groups)

    # export simple selection that can be serialized
    simple_selection = selection.exporter()

    # add simple selection to session
    request.session['selection'] = simple_selection

    # context
    context = selection.dict(selection_type)

    # amino acid groups
    amino_acid_groups = {
        'amino_acid_groups': definitions.AMINO_ACID_GROUPS,
        'amino_acid_group_names': definitions.AMINO_ACID_GROUP_NAMES }
    context.update(amino_acid_groups)

    # template to load
    template = 'common/selection_lists_sitesearch.html'

    return render(request, template, context)

def SelectResidueGroup(request):
    """Receives a selection request, updates the active residue group, and returns the updated selection"""
    selection_type = request.GET['selection_type']
    group_id = int(request.GET['group_id'])

    # get simple selection from session
    simple_selection = request.session.get('selection', False)

    # create full selection and import simple selection (if it exists)
    selection = Selection()
    if simple_selection:
        selection.importer(simple_selection)

    # update the selected group
    selection.active_site_residue_group = group_id

    # export simple selection that can be serialized
    simple_selection = selection.exporter()

    # add simple selection to session
    request.session['selection'] = simple_selection

    # context
    context = selection.dict(selection_type)

    # amino acid groups
    amino_acid_groups = {
        'amino_acid_groups': definitions.AMINO_ACID_GROUPS,
        'amino_acid_group_names': definitions.AMINO_ACID_GROUP_NAMES }
    context.update(amino_acid_groups)

    # template to load
    template = 'common/selection_lists_sitesearch.html'

    return render(request, template, context)

def RemoveResidueGroup(request):
    """Receives a selection request, removes a residue group, and returns the updated selection"""
    selection_type = request.GET['selection_type']
    group_id = int(request.GET['group_id'])

    # get simple selection from session
    simple_selection = request.session.get('selection', False)

    # create full selection and import simple selection (if it exists)
    selection = Selection()
    if simple_selection:
        selection.importer(simple_selection)

    # find all positions in the group and delete them
    for position in selection.segments:
        if position.type == 'site_residue':
            if position.properties['site_residue_group'] == group_id:
                selection.remove(selection_type, position.type, position.item.id)
            else:
                if position.properties['site_residue_group'] > group_id:
                    position.properties['site_residue_group'] -= 1

    # export simple selection that can be serialized
    simple_selection = selection.exporter()

    # add simple selection to session
    request.session['selection'] = simple_selection

    # context
    context = selection.dict(selection_type)

    # amino acid groups
    amino_acid_groups = {
        'amino_acid_groups': definitions.AMINO_ACID_GROUPS,
        'amino_acid_group_names': definitions.AMINO_ACID_GROUP_NAMES }
    context.update(amino_acid_groups)

    # template to load
    template = 'common/selection_lists_sitesearch.html'

    return render(request, template, context)

def SetGroupMinMatch(request):
    """Receives a selection request, sets a minimum match for a group, and returns the updated selection"""
    selection_type = request.GET['selection_type']
    group_id = int(request.GET['group_id'])
    min_match = int(request.GET['min_match'])

    # get simple selection from session
    simple_selection = request.session.get('selection', False)

    # create full selection and import simple selection (if it exists)
    selection = Selection()
    if simple_selection:
        selection.importer(simple_selection)

    # update the group
    selection.site_residue_groups[group_id - 1][0] = min_match

    # export simple selection that can be serialized
    simple_selection = selection.exporter()

    # add simple selection to session
    request.session['selection'] = simple_selection

    # context
    context = selection.dict(selection_type)

    # amino acid groups
    amino_acid_groups = {
        'amino_acid_groups': definitions.AMINO_ACID_GROUPS,
        'amino_acid_group_names': definitions.AMINO_ACID_GROUP_NAMES }
    context.update(amino_acid_groups)

    # template to load
    template = 'common/selection_lists_sitesearch.html'

    return render(request, template, context)

def ResiduesDownload(request):

    simple_selection = request.session.get('selection', False)

    outstream = BytesIO()
    wb = xlsxwriter.Workbook(outstream, {'in_memory': True})
    worksheet = wb.add_worksheet()
    row_count = 0

    for position in simple_selection.segments:
        if position.type == 'residue':
            worksheet.write_row(row_count, 0, ['residue', position.item.scheme.slug, position.item.label])
            row_count += 1
        elif position.type == 'helix':
            worksheet.write_row(row_count, 0, ['helix', '', position.item.slug])
            row_count += 1
    wb.close()
    outstream.seek(0)
    response = HttpResponse(outstream.read(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response['Content-Disposition'] = "attachment; filename=segment_selection.xlsx"

    return response

def ResiduesUpload(request):
    """Receives a file containing generic residue positions along with numbering scheme and adds those to the selection."""

    # get simple selection from session
    simple_selection = request.session.get('selection', False)

    # create full selection and import simple selection (if it exists)
    selection = Selection()
    if simple_selection:
        selection.importer(simple_selection)

    selection_type = 'segments'
    if request.FILES == {}:
        return render(request, 'common/selection_lists.html', '')

    #Overwriting the existing selection
    selection.clear(selection_type)

    #The excel file
    o = []
    workbook = xlrd.open_workbook(file_contents=request.FILES['xml_file'].read())
    worksheets = workbook.sheet_names()
    for worksheet_name in worksheets:
        worksheet = workbook.sheet_by_name(worksheet_name)
        for row in worksheet.get_rows():
            if len(row) < 2:
                continue
            try:
                if row[0].value == 'residue':
                    position = ResidueGenericNumberEquivalent.objects.get(label=row[2].value, scheme__slug=row[1].value)
                    o.append(position)
                elif row[0].value == 'helix':
                    o.append(ProteinSegment.objects.get(slug=row[2].value))
            except Exception as msg:
                print(msg)
                continue
    for obj in o:
        # add the selected item to the selection
        if obj.__class__.__name__ == 'ResidueGenericNumberEquivalent':
            selection_subtype = 'residue'
        else:
            selection_subtype = 'helix'
        selection_object = SelectionItem(selection_subtype, obj)
        selection.add(selection_type, selection_subtype, selection_object)

    # export simple selection that can be serialized
    simple_selection = selection.exporter()

    # add simple selection to session
    request.session['selection'] = simple_selection

    return render(request, 'common/selection_lists.html', selection.dict(selection_type))

@csrf_exempt
def ReadTargetInput(request):
    """Receives the data from the input form nd adds the listed targets to the selection"""

    # get simple selection from session
    simple_selection = request.session.get('selection', False)

    # create full selection and import simple selection (if it exists)
    selection = Selection()
    if simple_selection:
        selection.importer(simple_selection)

    selection_type = 'targets'
    selection_subtype = 'protein'

    if request.POST == {}:
        return render(request, 'common/selection_lists.html', '')

    o = []
    up_names = request.POST['input-targets'].split('\r')
    for up_name in up_names:
        try:
            o.append(Protein.objects.get(entry_name=up_name.strip().lower()))
        except:
            continue

    for obj in o:
        # add the selected item to the selection
        selection_object = SelectionItem(selection_subtype, obj)
        selection.add(selection_type, selection_subtype, selection_object)

    # export simple selection that can be serialized
    simple_selection = selection.exporter()

    # add simple selection to session
    request.session['selection'] = simple_selection

    # context
    context = selection.dict(selection_type)

    return render(request, 'common/selection_lists.html', context)

@csrf_exempt
def ExportExcelSuggestions(request):
    """Convert json file to excel file"""
    headers = ['reference','review', 'protein', 'mutation_pos', 'generic', 'mutation_from', 'mutation_to',
        'ligand_name', 'ligand_idtype', 'ligand_id', 'ligand_class',
        'exp_type', 'exp_func',  'exp_wt_value',  'exp_wt_unit','exp_mu_effect_sign', 'exp_mu_effect_type', 'exp_mu_effect_value',
        'exp_mu_effect_qual', 'exp_mu_effect_ligand_prop',  'exp_mu_ligand_ref', 'opt_type', 'opt_wt',
        'opt_mu', 'opt_sign', 'opt_percentage', 'opt_qual','opt_agonist', 'added_date'
         ] #'added_by',



    # icl2_end
    # thermo
    # icl2_start
    # nterm
    # cterm
    # icl3_start
    # removals
    # icl3_end

    sheets = ['nterm','icl2_start','icl2_end','icl3_start','icl3_end','cterm','thermo','removals']

    headers = {}
    headers['nterm'] = ['From TM1','hits','Homology levels','Fusions']
    headers['icl2_start'] = ['Start position','hits','Homology levels','PDB codes']
    headers['icl2_end'] = ['End position','hits','Homology levels','PDB codes']
    headers['icl3_start'] = ['Start position','hits','Homology levels','PDB codes']
    headers['icl3_end'] = ['End position','hits','Homology levels','PDB codes']
    headers['cterm'] = ['From TM8','hits','Homology levels']
    headers['thermo'] = ['Generic position','Mutation','hits','methods']
    headers['removals'] = ['segment','mutation','type','subtype']

    data = request.POST['d']
    data = json.loads(data)

    #EXCEL SOLUTION
    output = BytesIO()
    workbook = xlsxwriter.Workbook(output)

    for name in sheets:
        values = data[name]
        if len(values)==0:
            continue
        worksheet = workbook.add_worksheet(name)
        col = 0
        for h in headers[name]:
            worksheet.write(0, col, h)
            col += 1
        row = 1
        for d in values:
            col = 0
            #print(d)
            # for h in headers:
            #     worksheet.write(row, col, d[h])
            #     col += 1
            for c in d:
                #print(c)
                if isinstance(c, list):
                    c = ",".join(c)
                worksheet.write(row, col, c)
                col += 1
            row += 1
    workbook.close()
    output.seek(0)
    xlsx_data = output.read()

    ts = time.time()

    cache.set(ts,xlsx_data,30)
    response = HttpResponse(ts)
    return response

@csrf_exempt
def ExportExcelModifications(request):
    """Convert json file to excel file"""
    headers = ['#','type', 'method', 'range', 'info','insert_location','order','from','to','sequence','fixed','extra']

    #EXCEL SOLUTION
    output = BytesIO()
    workbook = xlsxwriter.Workbook(output)

    if 'd' in request.POST:
        data = request.POST['d']
        data = json.loads(data)

        worksheet = workbook.add_worksheet("modifications")
        row = 1
        index = {}
        col = 0
        for h in headers:
            worksheet.write(0, col, h)
            index[h] = col
            col += 1
        number = 0
        for mod in data:
            worksheet.write(row, 0, str(number))
            number += 1
            for m,v in mod.items():
                if isinstance(v, list) and m=='range' and len(v)>1:
                    #v = ",".join(str(x) for x in v)
                    v = str(v[0])+"-"+str(v[-1]) #first and last
                elif isinstance(v, list):
                    v = ",".join(str(x) for x in v)
                if m in index:
                    worksheet.write(row, index[m], str(v))
                else:
                    print('No column for '+m)
            row += 1
    elif 'm' in request.POST:
        datas = request.POST['m']
        datas = json.loads(datas)
        worksheet = workbook.add_worksheet("modifications")
        row = 0
        for i, data in enumerate(datas):

            worksheet.write(row, 0, "Construct Number #"+str(i+1))
            row += 1
            index = {}
            col = 0
            for h in headers:
                worksheet.write(row, col, h)
                index[h] = col
                col += 1
            number = 0
            row += 1
            for mod in data:
                if len(mod)>0:
                    worksheet.write(row, 0, str(number))
                    number += 1
                    for m,v in mod.items():
                        if isinstance(v, list) and m=='range' and len(v)>1:
                            #v = ",".join(str(x) for x in v)
                            v = str(v[0])+"-"+str(v[-1]) #first and last
                        elif isinstance(v, list):
                            v = ",".join(str(x) for x in v)
                        if m in index:
                            worksheet.write(row, index[m], str(v))
                        else:
                            print('No column for '+m)
                row += 1
            row += 1


    worksheet2 = workbook.add_worksheet("FASTA SEQUENCES")
    # worksheet2.write(0, 0, "sequences")
    row = 0

    sequences = request.POST['s']
    sequences = json.loads(sequences)
    for s in sequences:
        # worksheet2.write(row, 0, "Modifications # used")
        # worksheet2.write(row, 1, ' '.join(s[0]))
        # row += 1
        worksheet2.write(row, 0, ">" + s[1] + "[Modifications:" + ' '.join(s[0]) +"]")
        row += 1
        #worksheet2.write(row, 0, "Sequence")
        worksheet2.write(row, 0, s[2])
        row += 1

    worksheet3 = workbook.add_worksheet("FASTA SEQUENCES BLOCK")
    # worksheet2.write(0, 0, "sequences")
    row = 0

    sequences = request.POST['s']
    sequences = json.loads(sequences)
    for s in sequences:
        # worksheet2.write(row, 0, "Modifications # used")
        # worksheet2.write(row, 1, ' '.join(s[0]))
        # row += 1
        worksheet3.write(row, 0, ">" + s[1] + "[Modifications:" + ' '.join(s[0]) +"]")
        row += 1
        #worksheet2.write(row, 0, "Sequence")
        worksheet3.write(row, 0, s[3])
        row += 1

    workbook.close()
    output.seek(0)
    xlsx_data = output.read()

    ts = time.time()

    cache.set(ts,xlsx_data,30)
    response = HttpResponse(ts)
    return response

def ExportExcelDownload(request, ts, entry_name):
    """Convert json file to excel file"""

    response = HttpResponse(cache.get(ts),content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename='+entry_name+'.xlsx' #% 'mutations'

    return response

@csrf_exempt
def ImportExcel(request, **response_kwargs):
    """Recieves excel, outputs json"""
    o = []
    if request.method == 'POST':
        form = FileUploadForm(data=request.POST, files=request.FILES)
        if form.is_valid():
            workbook = xlrd.open_workbook(file_contents=request.FILES['file_source'].read())
            worksheets = workbook.sheet_names()
            for worksheet_name in worksheets:
                worksheet = workbook.sheet_by_name(worksheet_name)
                # Only load modifications
                if worksheet_name!='modifications':
                    continue
                num_rows = worksheet.nrows - 1
                num_cells = worksheet.ncols - 1
                curr_row = 0 #skip first, otherwise -1
                while curr_row < num_rows:
                    curr_row += 1
                    #row = worksheet.row(curr_row)
                    curr_cell = -1
                    temprow = []
                    if worksheet.cell_value(curr_row, 0) == '': #if empty
                        continue
                    while curr_cell < num_cells:
                        curr_cell += 1
                        #cell_type = worksheet.cell_type(curr_row, curr_cell)
                        cell_value = worksheet.cell_value(curr_row, curr_cell)
                        temprow.append(cell_value)
                    o.append(temprow)
            jsondata = json.dumps(o)
            response_kwargs['content_type'] = 'application/json'
            return HttpResponse(jsondata, **response_kwargs)

        else:
            pass
    jsondata = json.dumps(o)
    response_kwargs['content_type'] = 'application/json'
    return HttpResponse(jsondata, **response_kwargs)
