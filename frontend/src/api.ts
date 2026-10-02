let csrf = '';
export function setCsrf(value:string) { csrf=value; }
export async function api<T=any>(path:string, options:RequestInit={}):Promise<T> {
  const form=options.body instanceof FormData;
  const res=await fetch('/api'+path,{...options,credentials:'same-origin',headers:{...(!form&&options.body?{'Content-Type':'application/json'}:{}),...(csrf?{'X-CSRF-Token':csrf}:{}),...options.headers}});
  if(!res.ok) { const body=await res.json().catch(()=>({detail:`Erreur ${res.status}`})); throw new Error(typeof body.detail==='string'?body.detail:'Vérifiez les champs du formulaire.'); }
  return res.json();
}
export const json=(value:unknown)=>JSON.stringify(value);
