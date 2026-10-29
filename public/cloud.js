(function(root){
  'use strict';
  const {copy,merge}=root.PolkaMerge;
  root.PolkaCloud=function(hooks){
    let user=null,csrf='',base=null,revision=0,local=null,pending=false,busy=false,authenticated=false,generation=0,timer,dbPromise,writeQueue=Promise.resolve(),recover=[];
    let device;
    try{device=sessionStorage.getItem('polka-tab');if(!device){device=crypto.randomUUID();sessionStorage.setItem('polka-tab',device)}}catch{device=crypto.randomUUID()}
    const request=async(path,options={})=>{const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),20000);try{const response=await fetch(path,{...options,credentials:'same-origin',cache:'no-store',signal:controller.signal});const body=await response.json();if(!response.ok){const error=Error(body.error?.message||'Не удалось связаться с сервером.');error.status=response.status;error.body=body;throw error}return body}finally{clearTimeout(timeout)}};
    function db(){if(!dbPromise)dbPromise=new Promise((resolve,reject)=>{const r=indexedDB.open('polka-cloud',1);r.onupgradeneeded=()=>r.result.createObjectStore('records',{keyPath:'key'});r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error)});return dbPromise}
    async function records(){const d=await db();return new Promise((resolve,reject)=>{const r=d.transaction('records').objectStore('records').getAll();r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error)})}
    async function put(record){const d=await db();return new Promise((resolve,reject)=>{const t=d.transaction('records','readwrite');t.objectStore('records').put(record);t.oncomplete=()=>resolve();t.onerror=()=>reject(t.error);t.onabort=()=>reject(t.error)})}
    async function clearDraft(key,expected){const d=await db();return new Promise((resolve,reject)=>{const t=d.transaction('records','readwrite'),s=t.objectStore('records'),r=s.get(key);r.onsuccess=()=>{if(r.result?.generation===expected)s.delete(key)};t.oncomplete=()=>resolve();t.onerror=()=>reject(t.error)})}
    const key=()=>`draft:${user.id}:${device}`;
    function status(text,kind=''){hooks.status(text,kind)}
    function saveDraft(){if(!user||!local)return;const record={key:key(),owner:user.id,base:copy(base),local:copy(local),revision,generation};writeQueue=writeQueue.catch(()=>{}).then(()=>put(record));writeQueue.catch(()=>{hooks.unsaved(true);status('Не удалось сохранить на устройстве','error');hooks.notify('Хранилище устройства недоступно. Не закрывай вкладку до сохранения на сервере.')})}
    function apply(state){local=hooks.validate(copy(state));hooks.replace(copy(local))}
    function mergeIn(b,l,r){const m=merge(b,l,r);const checked=hooks.validate(m.state);if(m.conflicts)hooks.notify('Изменения объединены. Разные версии находки сохранены отдельно.');return checked}
    function changed(state){if(!user)return false;local=copy(state);pending=true;generation++;hooks.unsaved(true);status('Сохраняем…');saveDraft();clearTimeout(timer);timer=setTimeout(flush,450);return true}
    async function flush(){
      if(!user||!authenticated||busy||!pending||!local)return;
      busy=true;
      const sent=copy(local),sentGeneration=generation;
      try{
        const result=await request('/api/shelf',{method:'PUT',headers:{'Content-Type':'application/json','X-Polka-CSRF':csrf},body:JSON.stringify({revision,state:sent})});
        while(hooks.blocked())await new Promise(resolve=>setTimeout(resolve,250));
        const next=generation===sentGeneration?result.state:mergeIn(sent,local,result.state);
        base=copy(result.state);revision=result.revision;pending=generation!==sentGeneration;
        apply(next);
        await put({key:'remote:'+user.id,owner:user.id,base:copy(base),revision});
        if(pending)saveDraft();else{
          await writeQueue.catch(()=>{});
          await clearDraft(key(),sentGeneration);
          for(const draft of recover)await clearDraft(draft.key,draft.generation);
          recover=[];hooks.unsaved(false);status('Всё сохранено на сервере','ok');
        }
      }catch(error){
        if(error.status===409){while(hooks.blocked())await new Promise(resolve=>setTimeout(resolve,250));const remote=hooks.validate(error.body.state);apply(mergeIn(baseBeforeRequest,local,remote));base=copy(remote);revision=error.body.revision;pending=true;generation++;saveDraft();status('Объединяем изменения…');}
        else if(error.status===401){authenticated=false;status('Войди снова — изменения на устройстве','error');hooks.expired();}
        else{status(error.status===422||error.status===413?error.message:'Нет связи — изменения на устройстве','error');if(error.status===422||error.status===413)hooks.notify(error.message);}
      }finally{busy=false;if(pending&&authenticated) {clearTimeout(timer);timer=setTimeout(flush,3000)}}
    }
    // Captured before every request, including retries, for a true three-way merge.
    let baseBeforeRequest;
    const originalFlush=flush;
    flush=async()=>{if(busy)return;baseBeforeRequest=copy(base);return originalFlush()};
    async function pull(){
      if(!user||!authenticated||busy||hooks.blocked()||document.hidden)return;
      if(pending){flush();return}
      busy=true;
      try{const result=await request('/api/shelf?since='+revision);if(!hooks.blocked()&&!pending&&!result.unchanged){base=copy(result.state);revision=result.revision;apply(base);await put({key:'remote:'+user.id,owner:user.id,base:copy(base),revision});status('Всё сохранено на сервере','ok')}}catch(error){if(error.status===401){authenticated=false;hooks.expired();status('Войди снова','error')}else status('Нет связи с сервером','error')}finally{busy=false}
    }
    async function start(){
      hooks.loading(true);
      try{
        const session=await request('/api/session');hooks.account(session);
        if(!session.user){status('Коллекция в этом браузере');return}
        user=session.user;csrf=session.csrf;authenticated=true;
        try{localStorage.setItem('polka-last-user',JSON.stringify(user))}catch{}
        const stored=await records();recover=stored.filter(x=>x.owner===user.id&&x.key.startsWith('draft:'));
        const remote=await request('/api/shelf');base=remote.state?hooks.validate(remote.state):null;revision=remote.revision;
        if(!base){const localDraft=recover[0]?.local;local=hooks.validate(localDraft||await hooks.migrate());pending=true;generation++;apply(local);saveDraft();}
        else{local=copy(base);for(const draft of recover)local=mergeIn(draft.base,draft.local,local);pending=recover.length>0;generation++;apply(local);if(pending)saveDraft();}
        await put({key:'remote:'+user.id,owner:user.id,base:copy(base),revision});
        if(pending)flush();else{hooks.unsaved(false);status('Всё сохранено на сервере','ok')}
      }catch(error){
        try{
          if(!user){const hint=localStorage.getItem('polka-last-user');if(hint)user=JSON.parse(hint)}
          if(!user)throw error;
          const stored=await records(),cached=stored.find(x=>x.key==='remote:'+user.id);recover=stored.filter(x=>x.owner===user.id&&x.key.startsWith('draft:'));
          base=cached?.base||null;revision=cached?.revision||0;local=base?copy(base):recover[0]?.local;
          if(!local)throw error;
          for(const draft of recover)local=mergeIn(draft.base,draft.local,local);
          pending=recover.length>0;generation++;apply(local);hooks.account({user,configured:true});status('Нет связи — полка на устройстве','error');hooks.unsaved(pending);
        }catch{user=null;status('Сервер недоступен — локальная полка','error');hooks.account({user:null,configured:false})}
      }finally{hooks.loading(false)}
    }
    async function logout(){if(!user)return;await flush();if(pending){hooks.notify('Сначала дождись сохранения или выгрузи изменения. Выход пока отменён.');return}try{await request('/api/session',{method:'DELETE',headers:{'X-Polka-CSRF':csrf}});localStorage.removeItem('polka-last-user');location.reload()}catch(error){hooks.notify(error.message)}}
    document.addEventListener('visibilitychange',()=>{if(!document.hidden)pull()});
    window.addEventListener('focus',pull);window.addEventListener('online',()=>{if(!authenticated){location.reload();return}if(pending)flush();else pull()});
    setInterval(pull,8000);
    return {start,changed,logout,active:()=>!!user,pending:()=>pending,retry:()=>pending?flush():pull()};
  };
})(globalThis);
