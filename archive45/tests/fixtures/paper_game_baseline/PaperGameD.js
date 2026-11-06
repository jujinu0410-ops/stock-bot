/**
 * D SHADOW ONLY: ER(20) from first observed post-completion 45m quotes.
 * NOT an ER calculated from actual OHLCV close candles and NOT ATR(14).
 * Shared paper-only accounts. Uses existing synced four-ticker snapshots.
 */
function pgDMetric_(observed){
  if(!Array.isArray(observed)||observed.length<21)return null;
  const v=observed.slice(-21),changes=[];
  for(let i=1;i<v.length;i++){
    const p=Number(v[i]),q=Number(v[i-1]);
    if(!isFinite(p)||!isFinite(q)||p<=0||q<=0)return null;
    changes.push(Math.abs(p-q));
  }
  const travel=changes.reduce((s,x)=>s+x,0),net=Math.abs(v[20]-v[0]);
  const er=travel>0?net/travel:0;
  const rangeProxy=changes.slice(-14).reduce((s,x)=>s+x,0)/14;
  return {er:Math.max(0,Math.min(1,er)),rangeProxy:rangeProxy};
}
function pgDInit_(a){
  if(a.dHistory)return;
  a.dHistory={};a.dMode={};a.dAnchor={};a.dLastAdd={};
  a.dTrail={};a.dCooldown={};a.dRegimes=[];a.dReadiness={};
  a.dDayStart={day:'',nav:PG.INITIAL};a.dHaltDay='';
}
function pgDLoadSeed_(a,rows){
  pgDInit_(a);
  // Historical 45m closes are indicator warm-up ONLY; no historical trades.
  if(!Array.isArray(rows))return false;
  const snapshot={};
  for(const [code] of PG.PAIRS){
    const good=rows.filter(r=>String(r[1])===code)
      .map(r=>({t:String(r[0]),price:Number(r[2]),source:String(r[3]),
                quality:String(r[4]),purpose:String(r[5])}))
      .sort((x,y)=>x.t.localeCompare(y.t));
    if(good.length!==21||good.some((r,i)=>
        !/^\d{4}-\d\d-\d\d (?:09:45|10:30|11:15|12:00|12:45|13:30|14:15|15:00):00$/.test(r.t)||
        r.t.slice(0,10)>=PG.START||r.t.slice(0,10)<='2026-10-19'||
        !Number.isFinite(r.price)||r.price<=0||
        r.quality!=='VALID_45M_COMPLETE'||r.purpose!=='ER20_WARMUP_ONLY_NO_TRADES'||
        r.source.indexOf('YFINANCE_15M')!==0||
        (i>0&&r.t===good[i-1].t)))return false;
    if(good[20].t.slice(0,10)!=='2026-10-30')return false;
    snapshot[code]=good;
  }
  if(snapshot[PG.PAIRS[0][0]][20].t!==snapshot[PG.PAIRS[1][0]][20].t)return false;
  for(const [code] of PG.PAIRS){
    a.dHistory[code]=snapshot[code].map(r=>r.price);
    a.dReadiness[code]=21;
  }
  a.dSeedStatus='HISTORICAL_45M_CLOSE_VALIDATED';
  a.dSeedUntil=snapshot[PG.PAIRS[0][0]][20].t;
  return true;
}
function pgDStep_(a,group,bar,day){
  pgDInit_(a);
  const prices={},flags={};
  for(const c of PG.CODES){
    const r=group[c],price=Number(r&&r[6]),v9=Number(r&&r[4]),v26=Number(r&&r[5]);
    if(!r||!isFinite(price)||price<=0||!isFinite(v9)||!isFinite(v26))return;
    prices[c]=price;
    flags[c]=price>v9&&v9>v26&&r[7]==='GOLD';
  }
  a.dLiveBars=(a.dLiveBars||0)+1;
  let nav=pgNav_(a,prices);
  if(a.dDayStart.day!==day)a.dDayStart={day:day,nav:nav};
  const riskStop=nav<=a.dDayStart.nav*.97;
  if(riskStop && a.dHaltDay!==day){
    for(const c of PG.CODES){
      if(a.positions[c])pgSell_(a,c,a.positions[c].qty,prices[c],bar,day,'D_DAILY_RISK_EXIT');
    }
    a.dHaltDay=day;
  }
  nav=pgNav_(a,prices);
  const canAdd=a.dHaltDay!==day&&nav>a.dDayStart.nav*.97&&
    1-nav/Math.max(a.peak,nav)<.06;
  for(const [up,down,base] of PG.PAIRS){
    const hist=a.dHistory[up]||(a.dHistory[up]=[]);
    hist.push(prices[up]);
    if(hist.length>30)hist.shift();
    a.dReadiness[up]=Math.min(21,hist.length);
    const metric=pgDMetric_(hist);
    const prev=a.dMode[up]||'WARMUP';
    const mode=!metric?'WARMUP':metric.er<=.30?'BOX':metric.er>=.55?'TREND':'UNCERTAIN';
    const direction=flags[up]&&!flags[down]?up:flags[down]&&!flags[up]?down:'';
    let actions=[];
    if(!metric||!canAdd){
      if(a.dHaltDay===day)actions.push('DAILY_RISK_HALT');
      else if(!metric)actions.push('NEED_'+(21-hist.length)+'_MORE_OBSERVATIONS');
      else actions.push('ADDS_PAUSED');
    }else if(mode==='BOX'){
      // Re-entering BOX after TREND permits a fresh dual-leg cycle once.
      // Never repurchase a harvested leg on every subsequent BOX bar.
      if(prev!=='BOX'){
        a.dMode['BASE_'+up]=false;a.dMode['BASE_'+down]=false;
        a.dAnchor[up]=prices[up];
      }
      // Low directional efficiency: small dual exposure, trade only if a
      // meaningful observed move and actual net profit survive friction.
      for(const c of [up,down]){
        if(!a.positions[c]&&!a.dMode['BASE_'+c]){
          if(pgBuy_(a,c,base*.75,prices[c],bar,day,'D_BOX_INITIAL',prices,false)){
            a.dMode['BASE_'+c]=true;actions.push('BOX_BASE_'+c);
          }
        }
      }
      let anchor=a.dAnchor[up];
      if(!isFinite(anchor)||anchor<=0)a.dAnchor[up]=anchor=prices[up];
      const moved=prices[up]-anchor,spacing=Math.max(metric.rangeProxy*.75,prices[up]*.003);
      if(Math.abs(moved)>=spacing){
        const winner=moved>0?up:down,loser=moved>0?down:up;
        const wp=a.positions[winner],lp=a.positions[loser];
        let changed=false;
        if(wp&&wp.qty>=4&&prices[winner]/wp.avg-1>=.006){
          changed=pgSell_(a,winner,Math.floor(wp.qty*.25),prices[winner],bar,day,
            'D_BOX_GRID_HARVEST')||changed;
          if(changed)actions.push('GRID_HARVEST_'+winner);
        }
        if(lp&&lp.adds<2&&prices[loser]/lp.avg-1<=-.006){
          const added=pgBuy_(a,loser,base*.20,prices[loser],bar,day,
            'D_BOX_GRID_REBALANCE',prices,true);
          if(added){changed=true;actions.push('GRID_ADD_'+loser);}
        }
        if(changed)a.dAnchor[up]=prices[up];
      }
    }else if(mode==='TREND'&&direction){
      if(a.dCooldown[up]!==direction)a.dCooldown[up]='';
      const other=direction===up?down:up;
      if(a.positions[other]){
        if(pgSell_(a,other,a.positions[other].qty,prices[other],bar,day,
          'D_TREND_OPPOSITE_EXIT'))actions.push('OPPOSITE_EXIT_'+other);
      }
      const curr=a.positions[direction],p=prices[direction];
      if(!curr){
        if(a.dCooldown[up]!==direction&&
           pgBuy_(a,direction,base*1.5,p,bar,day,'D_TREND_ENTER',prices,false)){
          a.dTrail[direction]=p;a.dLastAdd[direction]=p;
          actions.push('TREND_ENTER_'+direction);
        }
      }else{
        a.dTrail[direction]=Math.max(a.dTrail[direction]||p,p);
        // Risk exits are checked at the first actually observed price;
        // do NOT imply exact stop-limit fills or backfill candle extremes.
        const stopWidth=Math.max(metric.rangeProxy*1.5,p*.012);
        if(p<a.dTrail[direction]-stopWidth){
          if(pgSell_(a,direction,curr.qty,p,bar,day,'D_TREND_TRAILING_EXIT')){
            a.dCooldown[up]=direction;actions.push('TRAIL_EXIT_'+direction);
          }
        }else if(curr.adds<2&&p-(a.dLastAdd[direction]||curr.avg)>=
                 Math.max(metric.rangeProxy*.75,p*.012)){
          if(pgBuy_(a,direction,base*.30,p,bar,day,'D_TREND_PYRAMID',prices,true)){
            a.dLastAdd[direction]=p;actions.push('PYRAMID_'+direction);
          }
        }
      }
    }else{
      actions.push(mode==='TREND'?'TREND_DIRECTION_UNCONFIRMED':'NO_NEW_TRADE');
    }
    a.dMode[up]=mode;
    a.dRegimes.push([day,bar,up,metric===null?'':metric.er,
      metric===null?'':metric.rangeProxy,mode,direction,actions.join('|')||'HOLD',
      hist.length,(a.dSeedStatus==='HISTORICAL_45M_CLOSE_VALIDATED'&&a.dLiveBars<=20)?
        'HIST_SEED_CLOSE_PLUS_OBS':'OBSERVED_QUOTE_PROXY_NOT_OHLC']);
  }
  const after=pgNav_(a,prices);
  a.peak=Math.max(a.peak,after);
  a.mdd=Math.max(a.mdd,1-after/a.peak);
  a.maxExposure=Math.max(a.maxExposure,pgExposure_(a,prices)/after);
  const unreal=PG.CODES.reduce((s,c)=>s+
    (a.positions[c]?a.positions[c].qty*(prices[c]-a.positions[c].avg):0),0);
  const ready=PG.PAIRS.every(([u])=>(a.dReadiness[u]||0)>=21);
  a.daily.push([day,'D',Math.round(a.cash),Math.round(pgExposure_(a,prices)),
    Math.round(after),after/PG.INITIAL-1,Math.round(a.peak),after/a.peak-1,
    Math.round(a.realized),Math.round(unreal),pgExposure_(a,prices)/after,
    ready?'D_ER20_OBS_PROXY':'D_WARMUP',ready?'VALID':'WARMUP']);
  a.maxDate=day;
}