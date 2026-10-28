/* 16 像素一格的程序化素材。地形坐标来自服务端，家具只作视觉装饰。 */
window.PixelScene = (() => {
  const cache = new Map();
  const ink = '#544b42', wood = '#a57550', cream = '#f9e9c9';
  function painter(g) {
    const r = (x,y,w,h,c) => { g.fillStyle=c; g.fillRect(Math.round(x),Math.round(y),Math.round(w),Math.round(h)); };
    const text = (s,x,y,c=ink,size=6) => { g.font=`${size}px "Microsoft YaHei", sans-serif`;g.textAlign='center';g.textBaseline='middle';g.fillStyle=c;g.fillText(s,x,y); };
    const box = (x,y,w,h,c,b=ink) => {r(x,y,w,h,b);r(x+1,y+1,w-2,h-2,c);};
    const shadow = (x,y,w,h) => r(x+2,y+3,w,h,'#00000018');
    const pot = (x,y,flower=false) => {r(x+2,y+8,7,5,'#ae7056');r(x+1,y+7,9,2,'#df9970');r(x+5,y,2,8,'#4f7953');r(x+1,y+2,5,3,'#7caa6c');r(x+7,y+1,4,4,'#638e59');if(flower){r(x+3,y,3,3,'#e9adab');r(x+8,y+2,3,3,'#f5da8e');}};
    const tree = (x,y,k=0) => {r(x+6,y+10,4,8,'#876249');r(x+2,y+3,12,10,'#456b51');r(x,y+5,16,6,'#456b51');r(x+3,y,10,10,k%2?'#68935e':'#759c68');r(x+1,y+4,13,6,'#7ea16a');r(x+4,y+1,6,3,'#94b779');r(x+11,y+8,3,4,'#557f56');};
    const windowPane = (x,y,w=22) => {box(x,y,w,12,'#abd0cf','#8d7257');r(x+2,y+2,w-4,7,'#c1e0dc');r(x+w/2,y+1,1,10,'#f8ecd2');r(x+1,y+6,w-2,1,'#f8ecd2');r(x-2,y+11,w+4,2,'#ab7e53');r(x,y+1,3,9,'#ecdbc1');r(x+w-3,y+1,3,9,'#ecdbc1');};
    const bed = (x,y,c='#c28c88') => {shadow(x,y,25,36);box(x,y,25,36,'#c49b6c','#8d684c');r(x+2,y+2,21,9,'#f9efd9');r(x+4,y+4,8,5,'#fff8e7');r(x+14,y+4,7,5,'#fff8e7');r(x+2,y+12,21,21,c);r(x+2,y+12,21,3,'#f1d9ba');r(x+4,y+17,1,13,'#ffffff25');r(x+20,y+17,1,13,'#00000012');r(x+1,y+34,3,4,'#856246');r(x+21,y+34,3,4,'#856246');};
    const rug = (x,y,w,h,c='#b98074') => {r(x,y,w,h,c);r(x+2,y+2,w-4,h-4,'#e4c7a0');r(x+4,y+4,w-8,h-8,c);for(let i=2;i<w;i+=4){r(x+i,y-1,1,2,'#e0ba96');r(x+i,y+h-1,1,2,'#e0ba96');}};
    const desk = (x,y,w=24) => {shadow(x,y,w,13);box(x,y,w,13,'#cfa374','#91684a');r(x+2,y+2,w-4,2,'#e3bf8e');r(x+2,y+12,3,5,'#8c654a');r(x+w-5,y+12,3,5,'#8c654a');box(x+5,y-5,12,8,'#526e70');r(x+7,y-3,8,4,'#a0c7b7');r(x+10,y+3,2,2,'#64594d');};
    const shelf = (x,y,w=35) => {
      g.save();g.translate(x,y);
      // 两层木书柜：顶板、暗色格口、层板、踢脚和柜脚，书脊有高低与厚薄变化。
      shadow(0,1,w,21);box(0,0,w,22,'#af8258','#79583f');
      r(2,2,w-4,3,'#dec093');r(3,6,w-6,6,'#65513f');r(3,14,w-6,6,'#65513f');
      const colors=['#b87967','#889a72','#d2b16b','#76999c','#a08ca7','#c99a7a'];
      for(let row=0;row<2;row++){
        let bx=4,index=0;
        while(bx<w-5){
          const bw=[3,4,2,3][(index+row)%4],bh=[5,6,4,6][(index+row*2)%4];
          if(bx+bw>w-3)break;
          const by=(row?20:12)-bh;
          r(bx,by,bw,bh,colors[(index+row*2)%colors.length]);
          r(bx,by,1,bh,'#ffffff28');r(bx+1,by+2,Math.max(1,bw-2),1,'#eee0b4');
          bx+=bw+1;index++;
        }
      }
      r(2,12,w-4,2,'#d6ad7d');r(2,20,w-4,2,'#d6ad7d');
      r(0,0,w,1,'#efd3a6');r(2,22,3,2,'#79583f');r(w-5,22,3,2,'#79583f');
      g.restore();
    };
    const label = (s,x,y,w) => {r(x-w/2,y-5,w,10,'#fff3d9dd');};
    return {r,box,shadow,text,pot,tree,windowPane,bed,rug,desk,shelf,label};
  }

  function bedrooms(rows) {
    const seen=new Set(), rooms=[];
    for(let y=0;y<26;y++)for(let x=0;x<26;x++){
      const key=`${x},${y}`;if(rows[y][x]!=='h'||seen.has(key))continue;
      const cells=[[x,y]];seen.add(key);
      for(let i=0;i<cells.length;i++){const [a,b]=cells[i];for(const [dx,dy]of[[1,0],[-1,0],[0,1],[0,-1]]){
        const nx=a+dx,ny=b+dy,k=`${nx},${ny}`;if(rows[ny]?.[nx]==='h'&&!seen.has(k)){seen.add(k);cells.push([nx,ny]);}
      }}
      const xs=cells.map(c=>c[0]),ys=cells.map(c=>c[1]);
      rooms.push({x:Math.min(...xs),y:Math.min(...ys),w:Math.max(...xs)-Math.min(...xs)+1,h:Math.max(...ys)-Math.min(...ys)+1});
    }
    return rooms;
  }

  function house(g, rows) {
    const {r,box,text,pot,windowPane,bed,rug,desk,shelf,label} = painter(g);
    const floors = {h:'#d7b28a',L:'#cca77e',K:'#e1caaa',D:'#dbbb93',b:'#c2d5cc',a:'#bdc9a2',w:'#ccd0c5','.':'#d8bb96',E:'#bc9a70'};
    for(let y=0;y<26;y++)for(let x=0;x<26;x++){
      const ch=rows[y][x]; const px=x*16,py=y*16;
      if(ch===' '){r(px,py,16,16,'#d2dfba');continue;}
      r(px,py,16,16,floors[ch]||'#d5b38c');
      if(['b','K','w'].includes(ch)){
        r(px,py,16,1,'#ffffff45');r(px,py,1,16,'#ffffff45');
        if((x+y)%2===0)r(px+7,py+7,2,2,'#a5b4a14d');
      }else if(ch!=='■'){
        r(px,py+15,16,1,'#9b72442a');r(px+((y%2)*8),py,1,15,'#92673c20');
        if((x*7+y*3)%5===0)r(px+3,py+7,5,1,'#f5dfb62c');
      }
    }
    // 先合并墙体轮廓，再统一描边，避免拐角/T 形交汇处重复叠画和多伸半格。
    const wallMask=new Uint8Array(416*416);
    const balconyEdge=(x,y)=>(x===25&&y>=18)||(y===25&&x>=19);
    const wall=(x,y)=>rows[y]?.[x]==='■'&&!balconyEdge(x,y);
    const fillWall=(x,y,w,h)=>{
      for(let b=y;b<y+h;b++)for(let a=x;a<x+w;a++){
        if(a>=0&&a<416&&b>=0&&b<416)wallMask[b*416+a]=1;
      }
    };
    for(let y=0;y<26;y++)for(let x=0;x<26;x++)if(wall(x,y)){
      const px=x*16+8,py=y*16+8;
      fillWall(px-3,py-3,6,6);
      if(wall(x-1,y))fillWall(px-8,py-3,8,6);
      if(wall(x+1,y))fillWall(px,py-3,8,6);
      if(wall(x,y-1))fillWall(px-3,py-8,6,8);
      if(wall(x,y+1))fillWall(px-3,py,6,8);
    }
    const masked=(x,y)=>x>=0&&x<416&&y>=0&&y<416&&wallMask[y*416+x];
    for(let y=0;y<416;y++)for(let x=0;x<416;x++)if(masked(x,y))r(x+2,y+3,1,1,'#7f634a33');
    for(let y=0;y<416;y++)for(let x=0;x<416;x++)if(masked(x,y)){
      const color=!masked(x,y-1)||!masked(x-1,y)?'#f6e2bc':
        (!masked(x,y+1)||!masked(x+1,y)?'#a07d5c':'#d5b58a');
      r(x,y,1,1,color);
    }
    // 窗户嵌在外墙中心线上，横窗和竖窗共用素材；内隔墙、门洞不放窗。
    const outerWindow = (cx,cy,width=30,vertical=false) => {
      const start=-width/2, end=width/2;
      for(let d=start;d<=end;d+=2){
        const x=Math.floor((cx+(vertical?0:d))/16),y=Math.floor((cy+(vertical?d:0))/16);
        const outside=(a,b)=>rows[b]?.[a]===undefined||rows[b][a]===' ';
        if(rows[y]?.[x]!=='■'||!(vertical?(outside(x-1,y)||outside(x+1,y)):(outside(x,y-1)||outside(x,y+1))))return;
      }
      g.save();g.translate(cx,cy);if(vertical)g.rotate(-Math.PI/2);
      windowPane(-width/2,-6,width);g.restore();
    };
    outerWindow(112,8,34);outerWindow(8,128,30,true);
    outerWindow(8,208,30,true);outerWindow(8,288,30,true);outerWindow(112,408,34);
    outerWindow(224,8,32);outerWindow(376,8,30);
    // 阳台外侧是开放栏杆：东侧接客厅外墙，南侧接卫浴隔墙。
    // 使用同一角柱收口，避免东南角叠出双柱。
    r(405,282,8,128,'#7f634a33');r(298,407,113,7,'#7f634a33');
    for(let y=296;y<408;y+=16){box(405,y-2,6,5,'#d9dfc4','#879780');}
    for(let x=312;x<408;x+=16){box(x-2,405,5,6,'#d9dfc4','#879780');}
    r(406,280,2,129,'#b7c5a3');r(410,280,2,129,'#879780');
    r(296,406,113,2,'#e5e8cf');r(296,410,113,2,'#879780');
    for(const [x,y]of[[408,280],[408,344],[408,408],[352,408],[296,408]]){
      box(x-4,y-4,9,9,'#b7c5a3','#748a79');r(x-3,y-3,7,2,'#f0ecd4');
    }
    // 五间卧室共用家具模板；盆栽落在床头旁的地面空角，避开书柜与门洞。
    bedrooms(rows).forEach(({x,y,w,h},i)=>{
      const px=x*16,py=y*16,c=['#b67f7a','#7e9a9b','#b5ad76','#a292ae','#b99572'][i%5];
      bed(px+5,py+17,c);
      rug(px+38,py+33,49,22,'#bd9d80');
      if(i===4){
        // 05 北门留空，书柜正向靠东墙，桌柜之间保留间隙。
        desk(px+39,py+18,26);
        shelf(px+76,py+26,19);
      }else{
        desk(px+58,py+33,26);
        shelf(px+49,py+3,35);
      }
      pot(px+6,py+2,true);
    });
    // 上方厨房，台面/灶台/冰箱保持在厨房边界内。
    box(180,20,82,13,'#d9d7bc','#997e5d');r(182,21,78,3,'#f5e7c8');
    box(188,23,22,8,'#8ba9a3');r(198,18,2,6,'#617f7a');
    // 简洁俯视灶台：浅色面板、两个深色炉头和小旋钮。
    box(180,36,22,28,'#ddd8bd','#997e5d');
    for(const y of [40,52]){
      r(185,y,6,8,'#646c61');r(184,y+1,8,6,'#646c61');
      r(186,y+2,4,4,'#b9bca6');r(197,y+3,2,2,'#8c927f');
    }
    box(246,40,18,31,'#e4e7d3','#939c8c');r(248,54,14,1,'#a4b1a0');pot(224,18);
    // 右侧客厅，大门前留下整条通道。
    rug(300,88,89,73,'#9d9b79');
    box(307,132,76,22,'#688879','#4d6c62');r(311,130,68,17,'#91a58a');
    r(311,131,4,21,'#a8b798');r(375,131,4,21,'#a8b798');r(342,132,1,14,'#658776');
    box(327,104,36,17,'#bb8e5e','#8d6748');r(332,108,9,7,'#f6e6bd');pot(348,102,true);
    desk(323,44,48);box(331,32,31,18,'#9dbeb0','#334d50');r(334,35,24,11,'#a6cbb6');
    // 客厅书柜靠北墙、电视左侧，空出大门到餐厅的横向通路。
    shelf(291,19,29);pot(389,19);
    // 中央开敞餐厅，左边五间卧室的门通往这里。
    rug(144,164,112,79,'#b99a71');
    [[159,174],[211,174],[159,220],[211,220]].forEach(([x,y])=>{box(x,y,18,18,'#a88159');r(x+2,y+2,14,5,'#c2a478');});
    box(167,188,60,37,'#bc8a5c','#815f45');r(170,191,54,4,'#d8ad7d');r(191,190,12,33,'#ecdbc0');
    [174,211].forEach(x=>{box(x,201,9,9,'#f3ead3','#d9cbaa');});pot(192,197,true);
    // 餐厅书柜收进厨房南墙下方的墙边，避开两侧门洞及中部通路。
    shelf(152,96,48);pot(244,97,true);
    // 下方双卫各 3×4 格。
    [176,240].forEach(x=>{box(x+3,340,18,30,'#eaf0dc','#9cad9d');r(x+6,343,12,24,'#a5c6c0');
      box(x+25,373,17,20,'#eef1df','#99ac9e');r(x+29,378,9,9,'#bdcdc1');});
    // 右下大阳台及独立洗衣角。
    box(333,324,46,22,'#b4956e','#8b7052');r(337,327,38,4,'#dfc59a');
    r(337,345,3,9,'#8b7052');r(373,345,3,9,'#8b7052');pot(365,322,true);
    // 洗衣区实际可用地面是 x=19..20；x=18 是卫浴隔墙，机器不得覆盖。
    [306,322].forEach(x=>{
      box(x,374,14,22,'#ece9d6','#959e92');r(x+2,376,10,3,'#d5d9c8');
      box(x+2,382,10,10,'#8eaaa5','#b4c7b8');r(x+4,384,5,5,'#bdd1c9');
    });
    // 东侧大门仍在 (25,12)，不改变室内外切换入口。
    box(400,193,13,27,'#a2ab7b','#886e4e');r(405,205,2,3,'#f1d48d');text('→',406,231,'#715d46',8);
    for(let y=0;y<26;y++)for(let x=0;x<26;x++)if(rows[y][x]==='X'){r(x*16+2,y*16+4,12,8,'#867b6b');text('×',x*16+8,y*16+8,'#ebc6a0',9);}
  }

  function town(g,rows) {
    const {r,box,text,pot,tree,label,shadow}=painter(g);
    for(let y=0;y<26;y++)for(let x=0;x<26;x++){
      const px=x*16,py=y*16,ch=rows[y][x];
      r(px,py,16,16,'#9fbb79');
      if((x*11+y*7)%3===0){r(px+3,py+5,2,1,'#7f9e61');r(px+8,py+11,2,2,'#b3c888');}
      if((x*3+y*11)%19===0){r(px+6,py+9,2,2,'#f6de9b');r(px+8,py+9,2,2,'#e9f0c2');}
      if(ch==='#'||ch==='E'){r(px,py,16,16,'#c9b896');r(px,py,16,1,'#e9d7b1');r(px,py,1,16,'#b8a383');if((x+y)%2===0)r(px+6,py+7,5,1,'#eaddb8');}
      if(ch==='W'){r(px,py,16,16,'#79aeb2');r(px,py+2,16,1,'#9cc9c4');r(px+3,py+9,7,1,'#b7d8cb');}
      if(ch==='F'){r(px,py,16,16,'#ad8b59');r(px,py+3,16,2,'#876a45');r(px,py+12,16,2,'#876a45');for(let i=2;i<16;i+=6){r(px+i,py+6,2,4,'#dad184');r(px+i-1,py+7,4,2,'#bcc77b');}}
      if(ch==='~'){r(px,py,16,16,'#bca080');if((x+y)%3===0){box(px+2,py+5,10,5,'#c28d68','#9c7357');r(px+6,py+5,1,5,'#906c53');}}
    }
    // 林带每格一棵像素树。
    for(let y=0;y<26;y++)for(let x=0;x<26;x++)if(rows[y][x]==='T')tree(x*16,y*16,(x+y)%2);
    // 建筑物按连通块生成，医院使用真实范围，不依赖过时手册。
    const seen=new Set();
    const styles={S:['商店','#b77d62','#e4be83'],C:['咖啡馆','#788d73','#e8ccb0'],O:['事务所','#759496','#e3dcc0'],R:['饭店','#b28265','#eed2a8'],U:['学校','#999677','#e4d8b5'],'~':['学校','#999677','#e4d8b5'],M:['小镇影院','#8c7c99','#d9c6b5'],G:['健身房','#a27964','#e5c5a0'],B:['诊所','#779c8d','#f0e7cf']};
    for(let y=0;y<26;y++)for(let x=0;x<26;x++){
      const ch=rows[y][x];if(!styles[ch]||seen.has(`${x},${y}`))continue;
      let todo=[[x,y]],cells=[];seen.add(`${x},${y}`);
      for(let i=0;i<todo.length;i++){const [a,b]=todo[i];cells.push([a,b]);for(const [dx,dy]of[[1,0],[-1,0],[0,1],[0,-1]]){const nx=a+dx,ny=b+dy,key=`${nx},${ny}`;if(rows[ny]?.[nx]===ch&&!seen.has(key)){seen.add(key);todo.push([nx,ny]);}}}
      const left=Math.min(...cells.map(a=>a[0]))*16,top=Math.min(...cells.map(a=>a[1]))*16;
      const w=(Math.max(...cells.map(a=>a[0]))+1)*16-left,h=(Math.max(...cells.map(a=>a[1]))+1)*16-top;
      const kind=ch==='O'&&y>=7&&y<=10?'R':ch;
      const [name,roof,wall]=styles[kind];
      shadow(left+3,top+6,w-5,h-5);box(left+3,top+6,w-6,h-9,wall,'#756b52');
      const roofHeight=Math.max(12,Math.round(h*.48)),eave=top+2+roofHeight;
      box(left+1,top+2,w-2,roofHeight,roof,'#685f50');
      for(let yy=top+5;yy<eave-2;yy+=5){r(left+3,yy,w-6,1,'#ffffff25');for(let xx=left+7+(yy%2)*3;xx<left+w-4;xx+=12)r(xx,yy,1,4,'#00000012');}
      r(left,eave,w,3,'#605e4c');
      const bottom=top+h-4;
      if(kind==='M'){
        for(let xx=left+10;xx<left+w-10;xx+=20){box(xx,bottom-18,12,12,'#88aaa7','#8b8469');r(xx+2,bottom-16,4,7,'#bdd5c5');r(xx+6,bottom-17,1,10,'#e5dabe');}
        box(left+w/2-6,bottom-17,12,17,'#856d53','#6e634f');r(left+w/2-4,bottom-15,8,7,'#a6b8a3');r(left+w/2+2,bottom-6,2,2,'#e4c486');
      }else{
        const doorX=left+Math.floor(w/2)-6,doorH=Math.min(17,bottom-eave-4);
        const winY=Math.max(eave+4,bottom-18),winH=Math.min(12,bottom-5-winY);
        // 左右各自分配窗户，门洞两边保留至少四像素，避开屋檐。
        const sideW=Math.floor(w/2)-18,count=Math.max(1,Math.floor((sideW+6)/20));
        const gap=(sideW-count*12)/(count+1);
        for(let i=0;i<count;i++){
          const offset=Math.round(6+gap+(12+gap)*i);
          for(const xx of [left+offset,left+w-offset-12]){
            box(xx,winY,12,winH,'#88aaa7','#8b8469');r(xx+2,winY+2,4,Math.max(2,winH-4),'#bdd5c5');r(xx+6,winY+1,1,winH-2,'#e5dabe');
          }
        }
        box(doorX,bottom-doorH,12,doorH,'#856d53','#6e634f');r(doorX+2,bottom-doorH+2,8,Math.min(7,doorH-5),'#a6b8a3');r(doorX+8,bottom-6,2,2,'#e4c486');
      }
      if(kind==='S'||kind==='C'||kind==='R'){for(let xx=left+4;xx<left+w-4;xx+=8)r(xx,bottom-24,8,6,(xx-left)%16<8?'#f1dfbc':roof);}
      label(name,left+w/2,top+13,name.length*6+12);
      if(ch==='B'){r(left+w-20,top+6,12,4,'#f7e9cd');r(left+w-16,top+2,4,12,'#f7e9cd');}
      if(kind==='O'){box(left+w-22,top+6,7,10,'#bdad8e','#756b58');r(left+w-23,top+4,9,3,'#d5c6a6');r(left+w-21,top+4,5,1,'#716e5c');r(left+w-20,top+9,4,1,'#d8c6a5');}
    }
    // 中央绿地：小径、喷泉、花坛与长椅。
    r(158,36,8,129,'#c6b58c');r(158,113,112,8,'#c6b58c');
    box(188,66,56,41,'#c3bda1','#78936c');box(192,70,48,33,'#8bb5ad','#e5d6b5');r(197,75,38,23,'#a5cac0');box(210,79,12,14,'#dcd8b7','#86a79b');r(214,73,4,9,'#d4e6d0');r(206,91,20,2,'#d3e1c7');
    [[179,40],[245,45],[181,133],[248,147]].forEach(([x,y],i)=>tree(x,y,i));
    [[190,125],[228,125]].forEach(([x,y])=>{r(x,y,23,7,'#a4835c');r(x,y+2,23,1,'#d6b082');r(x+3,y+6,2,5,'#736d52');r(x+18,y+6,2,5,'#736d52');});
    label('小镇花园',216,159,43);
    // 公园与湖，低矮围栏留出进入通道。
    [[38,231],[91,237],[41,282],[89,287]].forEach(([x,y],i)=>tree(x,y,i));
    r(65,238,6,61,'#c6b58e');r(41,267,62,6,'#c6b58e');
    for(let x=38;x<113;x+=8){r(x,303,2,7,'#e6d6aa');r(x,304,8,2,'#e6d6aa');}
    label('公园',78,287,29);r(70,351,7,3,'#6e9a7b');r(73,350,3,3,'#e8cbad');r(89,365,8,2,'#bfd4af');
    label('池塘',80,329,29);

    // 路灯、斑马线、回家指示。
    for(const x of[128,288])for(let i=0;i<4;i++)r(x+i*4,194,2,12,'#f0e1bb');
    [[122,180],[282,180],[122,300],[282,300]].forEach(([x,y])=>{r(x,y,2,15,'#686e59');r(x-3,y-2,8,5,'#d9b879');r(x-2,y-1,6,3,'#fae7a8');});
    box(18,178,32,12,'#f4e2bd','#8d805c');
    for(let y=0;y<26;y++)for(let x=0;x<26;x++)if(rows[y][x]==='X'){r(x*16+1,y*16+3,14,9,'#8c8770');text('×',x*16+8,y*16+8,'#ead4ab',9);}
  }

  function car(g,x,y,color,passengers=0) {
    const {r,box}=painter(g);
    r(x-9,y+6,20,4,'#00000025');r(x-8,y-4,4,5,'#4c5149');r(x+5,y-4,4,5,'#4c5149');r(x-8,y+5,4,5,'#4c5149');r(x+5,y+5,4,5,'#4c5149');
    box(x-9,y-3,19,12,color,'#586451');box(x-5,y-6,11,11,color,'#586451');r(x-3,y-4,7,4,'#c7dfcc');r(x-3,y+2,7,3,'#9cbbb4');r(x-8,y+1,2,4,'#fae2a1');
    for(let i=0;i<passengers;i++)r(x-4+i*3,y+6,2,2,'#fff0cc');
  }
  function character(g,x,y,c,gender,style='base',straightLegs=false,naturalHair=false,darkPants=false) {
    const {r,box}=painter(g),skin='#ecc7a0',hair='#66544a',shoe='#5b6059';
    const alignedLegs=()=>{
      for(const dx of [-4,2]){
        r(x+dx,y+8,3,3,gender==='男'&&darkPants?'#41443f':skin);
        r(x+dx,y+11,3,1,'#343735');
      }
    };
    if(gender==='女'){
      // 衣身最多九像素宽；手臂与手掌独立绘制，避免把下摆画成横条。
      const pony=style==='C',shortHair=style==='A'||style==='D';
      if(naturalHair){
        // 阶梯弧形头顶与不对称发梢，轮廓仍与男性头顶对齐。
        r(x-3,y-9,7,1,hair);r(x-4,y-8,9,1,hair);r(x-5,y-7,11,6,hair);
        r(x-5,y-1,3,2,hair);r(x+3,y-1,3,1,hair);
      }else r(x-5,y-8,11,shortHair?9:12,hair);
      if(pony){r(x+5,y-6,3,7,hair);r(x+6,y-5,2,3,'#7b6455');}
      if(straightLegs)alignedLegs();
      else{
        r(x-3,y+6,2,4,style==='C'?shoe:skin);r(x+2,y+6,2,4,style==='C'?shoe:skin);
        r(x-4,y+10,3,2,shoe);r(x+1,y+10,3,2,shoe);
      }
      // 短袖、肤色前臂和掌心；发型先画在后面，手不会被长发盖住。
      r(x-6,y,2,2,c);r(x+5,y,2,2,c);
      r(x-6,y+2,2,3,skin);r(x+5,y+2,2,3,skin);
      r(x-6,y+4,1,1,'#cda67f');r(x+6,y+4,1,1,'#cda67f');
      if(style==='B'){
        box(x-4,y-1,9,4,c,'#646550');box(x-3,y+2,7,4,c,'#646550');
        box(x-4,y+5,9,3,c,'#646550');
      }else if(style==='C'){
        box(x-4,y-1,9,8,c,'#646550');r(x-3,y+6,7,2,shoe);
      }else if(style==='D'){
        box(x-4,y-1,9,5,c,'#646550');r(x-3,y+4,7,1,'#f4dec0');
        box(x-4,y+5,9,3,c,'#646550');
      }else{
        box(x-4,y-1,9,9,c,'#646550');r(x-3,y+3,7,1,'#ffffff20');
      }
      r(x-2,y+1,5,1,'#ffffff35');
      if(naturalHair){
        r(x-4,y-7,9,6,skin);
        r(x-5,y-6,2,5,hair);r(x+4,y-6,2,4,hair);
        r(x-4,y-1,2,2,hair);r(x+4,y-2,1,2,hair);
        r(x-3,y-7,4,1,hair);r(x,y-6,2,1,hair);
        // 额角补发：连起两侧发际线，保留发卡与眼睛位置。
        r(x-4,y-7,1,1,hair);r(x+3,y-7,1,2,hair);
        r(x-2,y-8,5,1,'#7b6455');
        r(x+4,y-7,2,1,c);r(x+4,y-6,1,1,c);
      }else{
        r(x-4,y-9,9,8,skin);r(x-5,y-(straightLegs?9:10),11,straightLegs?2:3,hair);
        r(x-5,y-8,2,shortHair?8:10,hair);r(x+4,y-8,2,shortHair?8:10,hair);
        r(x-3,y-8,4,1,hair);
        // 发卡直接使用模型选择的原色，与衣服保持一致。
        r(x+4,y-8,2,1,c);r(x+4,y-7,1,1,c);
      }
      r(x-2,y-4,1,1,'#554e43');r(x+2,y-4,1,1,'#554e43');
    }else{
      if(straightLegs)alignedLegs();
      else{r(x-4,y+6,3,5,shoe);r(x+2,y+6,3,5,shoe);}
      r(x-7,y,2,2,c);r(x+6,y,2,2,c);
      r(x-7,y+2,2,4,skin);r(x+6,y+2,2,4,skin);
      r(x-7,y+5,1,1,'#cda67f');r(x+7,y+5,1,1,'#cda67f');
      box(x-5,y-1,11,9,c,'#646550');r(x-3,y+1,7,2,'#ffffff35');
      r(x-4,y-9,9,8,skin);r(x-5,y-9,10,3,hair);
      r(x-5,y-7,2,4,hair);r(x+4,y-7,2,4,hair);
      r(x-2,y-4,1,1,'#554e43');r(x+2,y-4,1,1,'#554e43');
    }
  }
  function resident(g,p,selected) {
    const {r,box,text}=painter(g), x=p.x*16+8,y=p.y*16+7;
    const c=/^#[a-f\d]{6}$/i.test(p.color)?p.color:'#83987b';
    if(selected){r(x-10,y+8,20,2,'#fbebae');r(x-11,y+5,2,5,'#fbebae');r(x+9,y+5,2,5,'#fbebae');}
    if((p.transport==='car'||p._inCar)&&p.car_id){car(g,x,y,c,p.passengers?.length||0);}
    else {
      const swimming=p.state==='doing'&&p.activity_emoji==='🏊';
      if(swimming){
        g.save();g.beginPath();g.rect(x-8,y-12,16,16);g.clip();character(g,x,y,c,p.gender,'A',true,true,true);g.restore();
        r(x-8,y+4,17,1,'#c1dfd5');r(x-6,y+6,13,1,'#98c9c5');
      }else{r(x-5,y+9,12,3,'#00000023');character(g,x,y,c,p.gender,'A',true,true,true);}
      if(p.state==='doing'&&p.activity_emoji==='🎣'){r(x+5,y-2,1,8,'#836348');r(x+6,y-5,1,4,'#836348');r(x+7,y-5,1,11,'#eee3c1');r(x+7,y+6,2,2,'#d28363');}
      if(p.transport==='bicycle'){box(x-9,y+6,6,6,'#c1c4a0','#5a6554');box(x+5,y+6,6,6,'#c1c4a0','#5a6554');r(x-4,y+6,10,1,'#725f4c');r(x,y+3,1,7,'#725f4c');}
    }
    // 名牌在最终分辨率下绘制，避免汉字被压成 5px 后不可读。
    if(p.state==='doing'){r(x+8,y-8,3,3,'#eff0bd');r(x+9,y-12,1,2,'#8ba178');}
  }
  return {preview(ctx,{gender='女',color='#598f9c',style='A',straightLegs=false,naturalHair=false,darkPants=false}={},scale=4){
    ctx.imageSmoothingEnabled=false;ctx.clearRect(0,0,ctx.canvas.width,ctx.canvas.height);
    ctx.save();ctx.scale(scale,scale);character(ctx,12,14,color,gender,style,straightLegs,naturalHair,darkPants);ctx.restore();
  },draw(ctx,rows,tp,scene,players,vehicles=[],selected=null){
    const key=scene+rows.join('');
    if(cache.get(scene)?.key!==key){
      const c=document.createElement('canvas');c.width=c.height=416;
      const g=c.getContext('2d');g.imageSmoothingEnabled=false;
      (scene==='house'?house:town)(g,rows);cache.set(scene,{key,canvas:c});
    }
    ctx.imageSmoothingEnabled=false;ctx.clearRect(0,0,ctx.canvas.width,ctx.canvas.height);
    ctx.drawImage(cache.get(scene).canvas,0,0,ctx.canvas.width,ctx.canvas.height);
    ctx.save();ctx.scale(tp/16,tp/16);
    if(scene==='town')for(const v of vehicles){const driver=players.find(p=>p.pid===v.driver_id);if(driver?.scene==='town'&&driver.x===v.x&&driver.y===v.y)continue;car(ctx,v.x*16+8,v.y*16+7,v.owner_id?'#8b9f9b':'#c49c6c');}
    players.filter(p=>p.scene===scene&&!p.vehicle_driver).sort((a,b)=>a.y-b.y).forEach(p=>{
      const v=vehicles.find(v=>v.id===p.car_id);
      resident(ctx,{...p,_inCar:scene==='town'&&v&&v.x===p.x&&v.y===p.y},p.pid===selected);
    });
    ctx.restore();
    const scale=tp/16;
    const labels=scene==='house' ? [
      ...bedrooms(rows).map((q,i)=>[`卧室 ${String(i+1).padStart(2,'0')}`,(q.x+q.w/2)*16,(q.y+q.h)*16-5]),
      ['厨房',224,73],['客厅',346,222],['餐厅',198,258],['卫浴 1',200,395],['卫浴 2',264,395],['阳台',356,367],['洗衣',322,402]
    ]:[['商店',80,45],['咖啡馆',80,125],['事务所',352,45],['饭店',352,125],['小镇影院',216,237],['健身房',352,237],['诊所',352,317],['小镇花园',216,159],['公园',78,287],['池塘',80,329],['田园',350,387],['学校',192,333],['← 合租之家',34,184]];
    ctx.textAlign='center';ctx.textBaseline='middle';ctx.font=`${scene==='house'?12:11}px "Microsoft YaHei", sans-serif`;
    for(const [s,x,y]of labels){const w=ctx.measureText(s).width+12;ctx.fillStyle='#fff3d9dd';ctx.fillRect(x*scale-w/2,y*scale-8,w,16);ctx.fillStyle=ink;ctx.fillText(s,x*scale,y*scale);}
    for(const p of players.filter(p=>p.scene===scene&&!p.vehicle_driver)){
      const x=(p.x*16+8)*scale,y=(p.y*16-8)*scale,w=ctx.measureText(p.name).width+10;
      ctx.fillStyle=p.pid===selected?'#4f6c53':'#fff2d9ed';ctx.fillRect(Math.round(x-w/2),Math.round(y-8),Math.ceil(w),16);
      ctx.fillStyle=p.pid===selected?'#fff1cb':ink;ctx.fillText(p.name,x,y);
    }
  }};
})();
