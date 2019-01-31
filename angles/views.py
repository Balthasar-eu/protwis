from django.shortcuts import render
from django.conf import settings
from django.views.generic import TemplateView, View
from django.http import HttpResponse, JsonResponse, HttpResponseRedirect
import copy

from django.views.decorators.cache import cache_page

import contactnetwork.pdb as pdb

from collections import OrderedDict


from structure.models import Structure
from residue.models import Residue
from angles.models import Angle

import numpy as np
from sklearn.decomposition import PCA
from numpy.core.umath_tests import inner1d
import io
import freesasa
import scipy.stats as stats

def load_pdb_var(pdb_code, var):
    """
    load string of pdb as pdb with a file handle. Would be nicer to do this
    directly, but no such function implemented in Bio PDB
    """
    parser = pdb.PDBParser(QUIET=True)
    with io.StringIO(var) as f:
        return parser.get_structure(pdb_code,f)
    
def recurse(entity,slist):
    """
    filter a pdb structure in a recursive way
    
    entity: the pdb entity, a structure should be given on the top level
    
    slist: the list of filter criterias, for each level.            
    """
    for subenty in entity.get_list():
        if not subenty.id in slist[0]: entity.detach_child(subenty.id)
        elif slist[1:]: recurse(subenty, slist[1:])

def cal_pseudo_CB(r):
    """
    Calculate pseudo CB for Glycin
    from Bio pdb faq
    """
    a =r['CA'].get_vector()
    n = r['N'].get_vector() - a
    c = r['C'].get_vector() - a
    rot = pdb.rotaxis(-np.pi*120.0/180.0, c)
    b = n.left_multiply(rot) + a
    return b.get_array()

def pca_line(pca,h, r=0):
    """
    Calculate the pca for h and return the first pc transformed back to
    the original coordinate system
    """
    if ((not r) if pca.fit_transform(h)[0][0] < 0 else r):
        return pca.inverse_transform(np.asarray([[-20,0,0],[20,0,0]]))
    else:return pca.inverse_transform(np.asarray([[20,0,0],[-20,0,0]])) 

def calc_angle(b,c):
    """
    Calculate the angle between c, b and the orthogonal projection of b
    to the x axis.
    """
    ba = -b
    bc = c + ba
    ba[:,0] = 0
    return np.degrees(np.arccos(inner1d(ba, bc) / (np.linalg.norm(ba,axis=1) * np.linalg.norm(bc,axis=1))))

def ca_cb_calc(ca,cb,pca):
    """
    Calcuate the angles between ca, cb and center axis
    """
    return calc_angle(pca.transform(ca),pca.transform(cb))

def axes_calc(h,p,pca):
    """
    Calculate the orthogonal projection of the CA to the helix axis
    which is moved to the mean of three consecutive amino acids
    """
    a = (np.roll(np.vstack((h,h[0])),1,axis=0)[:-1] + h + np.roll(np.vstack((h,h[-1])),-1,axis=0)[:-1])/3
    b = p.transform(h)
    b[:,1:] = p.transform(a)[:,1:]
    b = p.inverse_transform(b)
    return calc_angle(pca.transform(b),pca.transform(h))

def set_bfactor(chain,angles):
    """
    simple helper to set the bfactor of all residues by some value of a
    list
    """
    for r,an in zip(chain.get_list(),angles):
        for a in r: a.set_bfactor(an)

def qgen(x, qset):
    """
    Helper function to slice a list of all residues of a protein of the
    list of the residues of all proteins
    """
    start = False
    for i in range(len(qset)-1,0,-1):
        if not start and qset[i].protein_conformation.protein == x:
            start = i
        if start and qset[i].protein_conformation.protein != x:
            if start != len(qset)-1:
                del qset[start+1:]
                return qset[i+1:]
            return qset[i+1:]
    del qset[start+1:]
    return qset

def browsertest(request):
    """
    Show interaction heatmap
    """
    return render(request, 'angles/browsertest.html')



class testTemplate(TemplateView):

    template_name = "test.html"
    def get_context_data(self, **kwargs):
        dblist = []
        context = super(testTemplate, self).get_context_data(**kwargs)
        extra_pca = True
        
        ###########################################################################
        ############################ Helper  Functions ############################
        ###########################################################################
    
        failed = []
        
        # get preferred chain for PDB-code
        references = Structure.objects.filter(protein_conformation__protein__family__slug__startswith="001").exclude(refined=True).prefetch_related('pdb_code','pdb_data','protein_conformation__protein','protein_conformation__state').order_by('protein_conformation__protein')
        references = list(references)
        
        pids = [ref.protein_conformation.protein.id for ref in references]
        
        qset = Residue.objects.filter(protein_conformation__protein__id__in=pids)
        qset = qset.filter(generic_number__label__regex=r'^[1-7]x[0-9]+').order_by('-protein_conformation__protein','-generic_number__label')
        qset = list(qset.prefetch_related('generic_number', 'protein_conformation__protein','protein_conformation__state'))
        
        res_dict = {ref.pdb_code.index:qgen(ref.protein_conformation.protein,qset) for ref in references}
        
        #######################################################################
        ######################### Start of main loop ##########################
        #######################################################################
        
        angle_dict = [{},{},{},{}]
        median_dict = [{},{},{},{}]
        
        for reference in references:
            
            preferred_chain = reference.preferred_chain.split(',')[0]
            pdb_code = reference.pdb_code.index
            
            
            try:
                
                print(pdb_code)
    
                structure = load_pdb_var(pdb_code,reference.pdb_data.pdb)
                pchain = structure[0][preferred_chain]
                state_id = reference.protein_conformation.state.id
                
                #######################################################################
                ###################### prepare and evaluate query #####################
                
                db_reslist = res_dict[pdb_code]
                
                #######################################################################
                ######################### filter data from db #########################
                
                def reslist_gen(x):
                    try:
                        while db_reslist[-1].generic_number.label[0] == x:
                            yield db_reslist.pop()
                    except IndexError:
                        pass
                
                # when gdict is not needed the helper can be removed
                #db_tmlist = [[(' ',r.sequence_number,' ') for r in reslist_gen(x) if r.sequence_number in pchain and r.sequence_number < 1000] for x in ["1","2","3","4","5","6","7"]]
                db_helper = [[(r,r.sequence_number) for r in reslist_gen(x) if r.sequence_number in pchain and r.sequence_number < 1000] for x in ["1","2","3","4","5","6","7"]]
                gdict = {r[1]:r[0] for hlist in db_helper for r in hlist}
                db_tmlist = [[(' ',r[1],' ') for r in sl] for sl in db_helper]
                db_set = set(db_tmlist[0]+db_tmlist[1]+db_tmlist[2]+db_tmlist[3]+db_tmlist[4]+db_tmlist[5]+db_tmlist[6])
                
                #######################################################################
                ############################# filter  pdb #############################
                
                recurse(structure, [[0], preferred_chain, db_set])
                
                #######################################################################
                ############### Calculate the axes through the helices ################
                #######################################################################
                N = 3
                
                hres_list = [np.asarray([pchain[r]["CA"].get_coord() for r in sl], dtype=float) for sl in db_tmlist]
                h_cb_list = [np.asarray([pchain[r]["CB"].get_coord() if "CB" in pchain[r] else cal_pseudo_CB(pchain[r]) for r in sl], dtype=float) for sl in db_tmlist]
    
                # fast and fancy way to take the average of N consecutive elements
                hres_three = np.asarray([sum([h[i:-(len(h) % N) or None:N] for i in range(N)])/N for h in hres_list])
                
                #######################################################################
                ################################# PCA #################################
                #######################################################################
                
                helix_pcas = [PCA() for i in range(7)]
                [pca_line(helix_pcas[i], h,i%2) for i,h in enumerate(hres_three)]
                
                # extracellular part
                if extra_pca:
                    helices_mn = np.asarray([np.mean(h, axis=0) for h in hres_three])
                    pos_list = np.asarray([pca_line(PCA(), h[:len(h)//2:(-(i%2) or 1)]) for i,h in enumerate(hres_three)])
                    pos_list = pos_list - (np.mean(pos_list,axis=1)-helices_mn).reshape(-1,1,3)
                    
                    pca = PCA()
                    pca_line(pca, np.vstack(pos_list))
                else:
                    pca = PCA()
                    pca_line(pca, np.vstack(hres_three))
                
                #######################################################################
                ################################ Angles ###############################
                #######################################################################
                
                ########################### Axis to CA to CB ##########################
    
                b_angle = np.concatenate([ca_cb_calc(ca,cb,pca) for ca,cb in zip(hres_list,h_cb_list)]).round(3)
                
                #set_bfactor(pchain,angle)
                
                ######################### Axis to Axis to CA ##########################
                
                a_angle = np.concatenate([axes_calc(h,p,pca) for h,p in zip(hres_list,helix_pcas)]).round(3)
                
                #set_bfactor(pchain,angle2)
                
                # uncomment for bulk create, update
                # TODO: list comp + database search for all?
                
                ############## SASA 
                pdbstruct = freesasa.Structure("pymol_output/" + pdb_code +'angle_colored_axes.pdb')
                res = freesasa.calc(pdbstruct)
                
                asa_list = []
                oldnum   = -1
                for i in range(res.nAtoms()):
                    resnum = pdbstruct.residueNumber(i)
                    if resnum == oldnum:
                        asa_list[-1] += res.atomArea(i)
                    else:
                        asa_list.append(res.atomArea(i))
                        oldnum = resnum
                
                ################ HSE
                
                
                hse = pdb.HSExposure.HSExposureCB(structure[0])
                hselist = [x[1][1] if x[1][1] > 0 else 0 for x in hse ]
                
                
                ################ dblist gen
                if len(pchain) - len(hselist):
                    print("\033[91mLength mismatch hse", pdb_code, '\033[0m')
                    
                if len(pchain) - len(asa_list):
                    print("\033[91mLength mismatch sasa", pdb_code, '\033[0m')
                
                if np.isnan(np.sum(asa_list)):
                    print("\033[91mNAN sasa", pdb_code, '\033[0m')
                    
                if np.isnan(np.sum(hselist)):
                    print("\033[91mNAN hse", pdb_code, '\033[0m')
                    continue
                
                
                
                for res,a1,a2,asa,hse in zip(pchain,a_angle,b_angle,asa_list,hselist):
                    dblist.append([gdict[res.id[1]], a1, a2, reference,state_id-1,asa,hse])
                    if gdict[res.id[1]].generic_number.label not in angle_dict[state_id-1]:
                        angle_dict[state_id-1][gdict[res.id[1]].generic_number.label] = [round(a1,3)]
                    else:
                        angle_dict[state_id-1][gdict[res.id[1]].generic_number.label].append(round(a1,3))
                
                
                
                # comment for bulk create, update
                #this works in any case but is (really) slow.
                # Actually it is so slow that it is faster to delete the table
                # and bulk create it!
                #for res,a1,a2 in zip(pchain,a_angle,b_angle):
                #    Angle.objects.update_or_create(residue=gdict[res.id[1]], structure=reference, defaults={'angle':round(a1,3)})
        
            except Exception as e:
                print("ERROR!!", pdb_code, e)
                failed.append(pdb_code)
                continue
        
        for i in range(4):
            for key in angle_dict[i]:
                sortlist = sorted(angle_dict[i][key])
                listlen = len(sortlist)
                median_dict[i][key] = sortlist[listlen//2] if listlen % 2 else (sortlist[listlen//2] + sortlist[listlen//2-1])/2
        
        for i, res in enumerate(dblist):
            
            g = res[0]
            a = res[1]
            
            templist = copy.copy(angle_dict[res[4]][g.generic_number.label])
            del templist[templist.index(a)]
            
            std_test = abs(np.average(templist) - int(a))/np.std(templist)
            std_len  = len(templist)
            std = stats.t.cdf(std_test, df=std_len-1)
            dblist[i].append(0.501 if np.isnan(std) else std)

        
        
        dblist = [Angle(residue=g, diff_med=round(abs(median_dict[i][g.generic_number.label]-a1),3), angle=a1, b_angle=a2, structure=ref, sasa=round(asa,3), hse=hse, sign_med=round(sig,3)) for g,a1,a2,ref,i,asa,hse,sig in dblist]
        
        print("created list")
        print(len(dblist))

        #this works if the database is empty and is fast
        Angle.objects.bulk_create(dblist,batch_size=5000)
        
        #this works if no new additions are made and is fast, only Django 2.2
        #Angle.objects.bulk_update(dblist)

        return context

def get_angles(request):
    
    print("angles get called")


    # PDB files
    try:
        pdbs = request.GET.getlist('pdbs[]')
    except IndexError:
        print("boo")
        pdbs = []
    
    #print("2")

    pdbs = [pdb.upper() for pdb in pdbs]

    # Use generic numbers? Defaults to True.
    #generic = True
    query = Angle.objects.filter(structure__pdb_code__index=pdbs[0]).prefetch_related("residue__generic_number")
    # Get the relevant interactions
    # Initialize response dictionary
    data = {}
    data['data'] = [[q.residue.generic_number.label,q.residue.sequence_number, q.angle, q.diff_med, q.sign_med, q.hse, q.sasa] for q in query]

    # Create a consensus sequence.
    
    return JsonResponse(data)

def ServePDB(request, pdbname):
    structure=Structure.objects.filter(pdb_code__index=pdbname.upper())
    if structure.exists():
        structure=structure.get()
    else:
        quit()

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

#@cache_page(60*60*24)
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

#@cache_page(60*60*24)
def PdbTableData2(request):

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
        data_table += "<tr><td>{}</td><td>{}</td><td><span>{}</span></td><td>{}</td><td>{}</td><td><span>{}</span></td><td>{}</td><td>{}</td><td data-sort='0'><input class='form-check-input pdb_selected' type='checkbox' value='' onclick='thisPDB2(this);' long='{}'  id='{}'></tr>\n".format(r['class'],pdb_id,r['protein_long'],r['protein_family'],r['species'],r['state'],r['representative'],r['date'],r['protein_long'],pdb_id)
    data_table += "</tbody></table>"
    return HttpResponse(data_table)


