"""Pure observation-set operations for saved-model analysis; no SMT search."""
import bisect,gzip,json,math
from pathlib import Path
from reevaluate_round6_saved_candidates import require,object_hash,model_id
BYTE_BITS=[tuple(i for i in range(8) if n&(1<<i)) for n in range(256)]

def bits(mask):
    for byte,value in enumerate(mask.to_bytes((mask.bit_length()+7)//8,'little')):
        for bit in BYTE_BITS[value]:yield byte*8+bit
def union(states):
    z=o=0
    for a,b in states:z|=a;o|=b
    return z,o
def covered(state):return state[0]&state[1]
def observed(state):return state[0].bit_count()+state[1].bit_count()
def add(a,b):return a[0]|b[0],a[1]|b[1]
def gain_mask(base,model):return covered(add(base,model))&~covered(base)
def obs_id(state):return object_hash([hex(state[0]),hex(state[1])])
def dump_gz(path,rows):
    with gzip.open(path,'wt',encoding='utf-8') as f:
        for row in rows:f.write(json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n')
def load_gz(path):
    with gzip.open(path,'rt',encoding='utf-8') as f:return [json.loads(line) for line in f]
def load_run(path):
    rows=load_gz(Path(path)/'models.jsonl.gz')
    for r in rows:r['obs']=(int(r['zero'],16),int(r['one'],16))
    return rows
def prefix(rows,k):return union(r['obs'] for r in rows[:k])
def pool_at(rows,cutoff):
    result=[];seen=set()
    for r in rows:
        if r['completion']<=cutoff and r['model_id'] not in seen:seen.add(r['model_id']);result.append(r)
    return result
def first_arrivals(rows,total):
    first=[[-1]*total,[-1]*total];state=(0,0)
    for i,r in enumerate(rows):
        for v in (0,1):
            for bit in bits(r['obs'][v]&~state[v]):first[v][bit]=i
        state=add(state,r['obs'])
    return first
def quantile(xs,q):
    if not xs:return None
    xs=sorted(xs);p=(len(xs)-1)*q;i=int(p);return xs[i]+(xs[min(i+1,len(xs)-1)]-xs[i])*(p-i)
def arrival_class(index,completion,k,reference_time):
    if index is None:return 'right_censored_900','right_censored_900'
    return ('early_by_index' if index<=k else 'later_by_index'),('early_by_time' if completion<=reference_time else 'later_by_time')

def select_path(pool,keep_first,limit=100,anchor_limit=32):
    """Feasible set only; except full pair enumeration no optimality claim."""
    if not pool:return [],dict(pair_checks=0,pair_exact=True,empty=True)
    require(len({r['model_id'] for r in pool})==len(pool),'selection pool contains duplicate identities')
    for field in ['run','formula','seed']:
        require(len({r[field] for r in pool if field in r})<=1,'selection pool mixes '+field)
    n=len(pool);checks=0
    if keep_first:chosen=[0]
    elif n==1:chosen=[0]
    else:
        anchors=sorted({round(i*(n-1)/(min(anchor_limit,n)-1)) for i in range(min(anchor_limit,n))})
        best=None;pair=None
        for i in anchors:
            for j in range(n):
                if i==j:continue
                checks+=1;u=add(pool[i]['obs'],pool[j]['obs']);a,b=sorted((i,j));key=(covered(u).bit_count(),observed(u),-a,-b)
                if best is None or key>best:best=key;pair=[a,b]
        chosen=pair
    state=union(pool[i]['obs'] for i in chosen);used=set(chosen)
    while len(chosen)<min(limit,n):
        best=None;winner=None
        for i,r in enumerate(pool):
            if i in used:continue
            u=add(state,r['obs']);key=(covered(u).bit_count(),observed(u),-i)
            if best is None or key>best:best=key;winner=i
        if best[0]==covered(state).bit_count() and best[1]==observed(state):break
        if keep_first and len(chosen)==1:checks+=n-1
        chosen.append(winner);used.add(winner);state=add(state,pool[winner]['obs'])
    return chosen,dict(pair_checks=checks,pair_exact=keep_first or n<=anchor_limit,empty=False)

def selection_bounds(pool,indices,k,keep_first,pair_exact):
    state=union(pool[i]['obs'] for i in indices[:k]);allstate=union(r['obs'] for r in pool)
    lower=covered(state).bit_count();upper=covered(allstate).bit_count()
    if k==2 and pair_exact:upper=lower
    return state,lower,upper,'optimal_fixed_pool' if lower==upper else 'feasible_lower_bound_unknown_optimum'

def selection_verdict(lower,upper,pool_upper,target):
    if lower>=target:return 'achieved_total_count'
    if upper<target:return 'proved_pool_insufficient' if pool_upper<target else 'proved_same_k_insufficient'
    return 'unknown'

def categories(layout,mask):
    rows={}
    for n in layout:
        local=(mask>>n['offset'])&((1<<n['width'])-1)
        for low,high,label in ([(0,0,'Boolean')] if n['width']==1 else [(0,7,'0-7'),(8,31,'8-31'),(32,63,'32-63')]):
            count=((local>>low)&((1<<(high-low+1))-1)).bit_count()
            if count:rows[n['kind'],label]=rows.get((n['kind'],label),0)+count
    return rows

def observation_events(layout,base,model):
    new=(model[0]&~base[0],model[1]&~base[1]);first_visit=0;nodes=0
    for n in layout:
        mask=((1<<n['width'])-1)<<n['offset']
        if not ((base[0]|base[1])&mask) and (model[0]|model[1])&mask:first_visit|=mask;nodes+=1
    a=(new[0]|new[1])&first_visit
    c=gain_mask(base,model)
    b=(new[0]|new[1])&~(a|c)
    require(not(a&b or a&c or b&c),'overlapping event categories')
    require((a|b|c).bit_count()==observed(add(base,model))-observed(base),'new observation partition')
    return dict(first_visit=a,first_single_side=b,complete_opposite=c),nodes
