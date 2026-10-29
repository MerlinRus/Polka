(function(root){
  'use strict';
  const copy=value=>JSON.parse(JSON.stringify(value));
  const equal=(a,b)=>JSON.stringify(a&&Object.keys(a).sort().map(k=>[k,a[k]]))===JSON.stringify(b&&Object.keys(b).sort().map(k=>[k,b[k]]));
  // Merge against the last acknowledged version, never against arrival time.
  // Conflicting content is retained as a second item; positions can use the local move.
  function merge(base,local,remote,id=()=>crypto.randomUUID()){
    if(!remote)return {state:copy(local),conflicts:0};
    if(!base)return {state:copy(remote),conflicts:0};
    let conflicts=0;
    const result={version:1,worlds:[],items:[]};
    for(const group of ['worlds','items']){
      const maps=[base,local,remote].map(s=>new Map(s[group].map(x=>[x.id,x])));
      const ids=new Set([...remote[group],...local[group],...base[group]].map(x=>x.id));
      for(const key of ids){
        const [b,l,r]=maps.map(m=>m.get(key));
        let value;
        if(equal(l,b))value=r;
        else if(equal(r,b)||equal(l,r))value=l;
        else if(!l||!r){value=l||r;conflicts++;}
        else{
          value={...r};let contentConflict=false;
          for(const field of new Set([...Object.keys(l),...Object.keys(r)])){
            if(!equal({v:l[field]},{v:b?.[field]})){
              if(!equal({v:r[field]},{v:b?.[field]})&&!equal({v:r[field]},{v:l[field]})&&!['x','y','r','favorite','created'].includes(field))contentConflict=true;
              if(field in l)value[field]=l[field];else delete value[field];
            }
          }
          if(contentConflict){conflicts++;if(group==='items')result.items.push({...copy(r),id:id(),title:(r.title+' (другая версия)').slice(0,120),x:Math.min(930,(r.x||30)+20),y:Math.min(16000,(r.y||50)+20)});}
        }
        if(value)result[group].push(copy(value));
      }
    }
    // A world can be deleted while a different device adds a finding to it.
    const worlds=new Set(result.worlds.map(w=>w.id));
    for(const item of result.items)if(!worlds.has(item.world)){
      const world=[...local.worlds,...remote.worlds,...base.worlds].find(w=>w.id===item.world);
      if(world){result.worlds.push(copy(world));worlds.add(world.id);conflicts++;}
    }
    if(!result.worlds.length)result.worlds.push(copy(remote.worlds[0]||local.worlds[0]));
    return {state:result,conflicts};
  }
  root.PolkaMerge={merge,equal,copy};
})(globalThis);
