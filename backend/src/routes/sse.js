const router = require('express').Router();
const sse = require('../helper/sse-client');
const gateService = require('../services/gateService');
const uuidv4 = require('uuid/v4');

let connections = [];

router.get('/', async (req, res) => {
    sse(req, res);
    const id = uuidv4();
    connections.push({id, res});
    console.log('new connection: ' + id);
    res.sse.sendEvent('state', await gateService.getAllGates());
    req.connection.on('close', () => {
        console.log('remove closed connection: ' + id);
        connections = connections.filter((con) => con.id !== id)
    });
});

async function notifyStateChange() {
    const gates = await gateService.getAllGates();
    connections.forEach(con => {
        console.log('notify connection: ' + con.id);
        con.res.sse.sendEvent('state', gates)
    });
}

module.exports = { router, notifyStateChange };