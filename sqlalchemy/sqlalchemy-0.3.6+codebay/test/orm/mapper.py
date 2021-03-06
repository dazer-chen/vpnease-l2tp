from testbase import PersistTest, AssertMixin
import testbase
import unittest, sys, os
from sqlalchemy import *
import sqlalchemy.exceptions as exceptions
from sqlalchemy.ext.sessioncontext import SessionContext
from tables import *
import tables

"""tests general mapper operations with an emphasis on selecting/loading"""

class MapperSuperTest(AssertMixin):
    def setUpAll(self):
        tables.create()
        tables.data()
    def tearDownAll(self):
        tables.drop()
    def tearDown(self):
        clear_mappers()
    def setUp(self):
        pass
    
class MapperTest(MapperSuperTest):
    def testget(self):
        s = create_session()
        mapper(User, users)
        self.assert_(s.get(User, 19) is None)
        u = s.get(User, 7)
        u2 = s.get(User, 7)
        self.assert_(u is u2)
        s.clear()
        u2 = s.get(User, 7)
        self.assert_(u is not u2)

    def testunicodeget(self):
        """test that Query.get properly sets up the type for the bind parameter.  using unicode would normally fail 
        on postgres, mysql and oracle unless it is converted to an encoded string"""
        metadata = BoundMetaData(db)
        table = Table('foo', metadata, 
            Column('id', Unicode(10), primary_key=True),
            Column('data', Unicode(40)))
        try:
            table.create()
            class LocalFoo(object):pass
            mapper(LocalFoo, table)
            crit = 'petit voix m\xe2\x80\x99a '.decode('utf-8')
            print repr(crit)
            create_session().query(LocalFoo).get(crit)
        finally:
            table.drop()

    def testpropconflict(self):
        """test that a backref created against an existing mapper with a property name
        conflict raises a decent error message"""
        mapper(Address, addresses)
        mapper(User, users,
            properties={
            'addresses':relation(Address, backref='email_address')
        })
        try:
            class_mapper(Address)
            class_mapper(User)
            assert False
        except exceptions.ArgumentError:
            pass

    def testbadcascade(self):
        mapper(Address, addresses)
        try:
            mapper(User, users, properties={'addresses':relation(Address, cascade="fake, all, delete-orphan")})
            assert False
        except exceptions.ArgumentError, e:
            assert str(e) == "Invalid cascade option 'fake'"
        
    def testcolumnprefix(self):
        mapper(User, users, column_prefix='_')
        s = create_session()
        u = s.get(User, 7)
        assert u._user_name=='jack'
    	assert u._user_id ==7
        assert not hasattr(u, 'user_name')
          
    def testrefresh(self):
        mapper(User, users, properties={'addresses':relation(mapper(Address, addresses))})
        s = create_session()
        u = s.get(User, 7)
        u.user_name = 'foo'
        a = Address()
        import sqlalchemy.orm.session
        assert sqlalchemy.orm.session.object_session(a) is None
        u.addresses.append(a)

        self.assert_(a in u.addresses)

        s.refresh(u)
        
        # its refreshed, so not dirty
        self.assert_(u not in s.dirty)
        
        # username is back to the DB
        self.assert_(u.user_name == 'jack')
        
        self.assert_(a not in u.addresses)
        
        u.user_name = 'foo'
        u.addresses.append(a)
        # now its dirty
        self.assert_(u in s.dirty)
        self.assert_(u.user_name == 'foo')
        self.assert_(a in u.addresses)
        s.expire(u)

        # get the attribute, it refreshes
        self.assert_(u.user_name == 'jack')
        self.assert_(a not in u.addresses)
    
    def testexpirecascade(self):
        mapper(User, users, properties={'addresses':relation(mapper(Address, addresses), cascade="all, refresh-expire")})
        s = create_session()
        u = s.get(User, 8)
        u.addresses[0].email_address = 'someotheraddress'
        s.expire(u)
        assert u.addresses[0].email_address == 'ed@wood.com'
        
    def testrefreshwitheager(self):
        """test that a refresh/expire operation loads rows properly and sends correct "isnew" state to eager loaders"""
        mapper(User, users, properties={'addresses':relation(mapper(Address, addresses), lazy=False)})
        s = create_session()
        u = s.get(User, 8)
        assert len(u.addresses) == 3
        s.refresh(u)
        assert len(u.addresses) == 3

        s = create_session()
        u = s.get(User, 8)
        assert len(u.addresses) == 3
        s.expire(u)
        assert len(u.addresses) == 3
        
    def testbadconstructor(self):
        """test that if the construction of a mapped class fails, the instnace does not get placed in the session"""
        class Foo(object):
            def __init__(self, one, two):
                pass
        mapper(Foo, users)
        sess = create_session()
        try:
            Foo('one', _sa_session=sess)
            assert False
        except:
            assert len(list(sess)) == 0
        try:
            Foo('one')
            assert False
        except TypeError, e:
            pass
            
    def testrefresh_lazy(self):
        """test that when a lazy loader is set as a trigger on an object's attribute (at the attribute level, not the class level), a refresh() operation doesnt fire the lazy loader or create any problems"""
        s = create_session()
        mapper(User, users, properties={'addresses':relation(mapper(Address, addresses))})
        q2 = s.query(User).options(lazyload('addresses'))
        u = q2.selectfirst(users.c.user_id==8)
        def go():
            s.refresh(u)
        self.assert_sql_count(db, go, 1)

    def testexpire(self):
        """test the expire function"""
        s = create_session()
        mapper(User, users, properties={'addresses':relation(mapper(Address, addresses), lazy=False)})
        u = s.get(User, 7)
        assert(len(u.addresses) == 1)
        u.user_name = 'foo'
        del u.addresses[0]
        s.expire(u)
        # test plain expire
        self.assert_(u.user_name =='jack')
        self.assert_(len(u.addresses) == 1)
        
        # we're changing the database here, so if this test fails in the middle,
        # it'll screw up the other tests which are hardcoded to 7/'jack'
        u.user_name = 'foo'
        s.flush()
        # change the value in the DB
        users.update(users.c.user_id==7, values=dict(user_name='jack')).execute()
        s.expire(u)
        # object isnt refreshed yet, using dict to bypass trigger
        self.assert_(u.__dict__.get('user_name') != 'jack')
        # do a select
        s.query(User).select()
        # test that it refreshed
        self.assert_(u.__dict__['user_name'] == 'jack')
        
        # object should be back to normal now, 
        # this should *not* produce a SELECT statement (not tested here though....)
        self.assert_(u.user_name =='jack')
        
    def testrefresh2(self):
        """test a hang condition that was occuring on expire/refresh"""
        s = create_session()
        mapper(Address, addresses)

        mapper(User, users, properties = dict(addresses=relation(Address,private=True,lazy=False)) )

        u=User()
        u.user_name='Justin'
        a = Address()
        a.address_id=17  # to work around the hardcoded IDs in this test suite....
        u.addresses.append(a)
        s.flush()
        s.clear()
        u = s.query(User).selectfirst()
        print u.user_name

        #ok so far
        s.expire(u)        #hangs when
        print u.user_name #this line runs

        s.refresh(u) #hangs
        
    def testmagic(self):
        """not sure what this is really testing."""
        mapper(User, users, properties = {
            'addresses' : relation(mapper(Address, addresses))
        })
        sess = create_session()
        l = sess.query(User).select_by(user_name='fred')
        self.assert_result(l, User, *[{'user_id':9}])
        u = l[0]
        
        u2 = sess.query(User).get_by_user_name('fred')
        self.assert_(u is u2)
        
        l = sess.query(User).select_by(email_address='ed@bettyboop.com')
        self.assert_result(l, User, *[{'user_id':8}])

        l = sess.query(User).select_by(User.c.user_name=='fred', addresses.c.email_address!='ed@bettyboop.com', user_id=9)

    def testprops(self):
        """tests the various attributes of the properties attached to classes"""
        m = mapper(User, users, properties = {
            'addresses' : relation(mapper(Address, addresses))
        }).compile()
        self.assert_(User.addresses.property is m.props['addresses'])
        
    def testload(self):
        """tests loading rows with a mapper and producing object instances"""
        mapper(User, users)
        l = create_session().query(User).select()
        self.assert_result(l, User, *user_result)
        l = create_session().query(User).select(users.c.user_name.endswith('ed'))
        self.assert_result(l, User, *user_result[1:3])

    def testrecursiveselectby(self):
        """test that no endless loop occurs when traversing for select_by"""
        m = mapper(User, users, properties={
            'orders':relation(mapper(Order, orders), backref='user'),
            'addresses':relation(mapper(Address, addresses), backref='user'),
        })
        q = create_session().query(m)
        q.select_by(email_address='foo')

    def testmappingtojoin(self):
        """test mapping to a join"""
        usersaddresses = sql.join(users, addresses, users.c.user_id == addresses.c.user_id)
        m = mapper(User, usersaddresses, primary_key=[users.c.user_id])
        q = create_session().query(m)
        l = q.select()
        self.assert_result(l, User, *user_result[0:2])
        
    def testmappingtoouterjoin(self):
        """test mapping to an outer join, with a composite primary key that allows nulls"""
        result = [
        {'user_id' : 7, 'address_id' : 1},
        {'user_id' : 8, 'address_id' : 2},
        {'user_id' : 8, 'address_id' : 3},
        {'user_id' : 8, 'address_id' : 4},
        {'user_id' : 9, 'address_id':None}
        ]
        
        j = join(users, addresses, isouter=True)
        m = mapper(User, j, allow_null_pks=True, primary_key=[users.c.user_id, addresses.c.address_id])
        q = create_session().query(m)
        l = q.select()
        self.assert_result(l, User, *result)
        
    def testjoinvia(self):
        """test the join_via and join_to functions"""
        m = mapper(User, users, properties={
            'orders':relation(mapper(Order, orders, properties={
                'items':relation(mapper(Item, orderitems))
            }))
        })

        sess = create_session()
        q = sess.query(m)

        l = q.filter(orderitems.c.item_name=='item 4').join(['orders', 'items']).list()
        self.assert_result(l, User, user_result[0])
        
        l = q.select_by(item_name='item 4')
        self.assert_result(l, User, user_result[0])

        l = q.filter(orderitems.c.item_name=='item 4').join('item_name').list()
        self.assert_result(l, User, user_result[0])

        l = q.filter(orderitems.c.item_name=='item 4').join('items').list()
        self.assert_result(l, User, user_result[0])

        # test comparing to an object instance
        item = sess.query(Item).get_by(item_name='item 4')
        l = q.select_by(items=item)
        self.assert_result(l, User, user_result[0])
    
        try:
            # this should raise AttributeError
            l = q.select_by(items=5)
            assert False
        except AttributeError:
            assert True

    def testjoinviam2m(self):
        """test the join_via and join_to functions"""
        m = mapper(Order, orders, properties = {
            'items' : relation(mapper(Item, orderitems, properties = {
                'keywords' : relation(mapper(Keyword, keywords), itemkeywords)
            }))
        })
        
        sess = create_session()
        q = sess.query(m)

        l = q.filter(keywords.c.name=='square').join(['items', 'keywords']).list()
        self.assert_result(l, Order, order_result[1])

        
    def testcustomjoin(self):
        """test that the from_obj parameter to query.select() can be used
        to totally replace the FROM parameters of the generated query."""
        m = mapper(User, users, properties={
            'orders':relation(mapper(Order, orders, properties={
                'items':relation(mapper(Item, orderitems))
            }))
        })

        q = create_session().query(m)
        l = q.select((orderitems.c.item_name=='item 4'), from_obj=[users.join(orders).join(orderitems)])
        self.assert_result(l, User, user_result[0])
            
    def testorderby(self):
        """test ordering at the mapper and query level"""
        # TODO: make a unit test out of these various combinations
#        m = mapper(User, users, order_by=desc(users.c.user_name))
        mapper(User, users, order_by=None)
#        mapper(User, users)
        
#        l = create_session().query(User).select(order_by=[desc(users.c.user_name), asc(users.c.user_id)])
        l = create_session().query(User).select()
#        l = create_session().query(User).select(order_by=[])
#        l = create_session().query(User).select(order_by=None)
        
        
    @testbase.unsupported('firebird') 
    def testfunction(self):
        """test mapping to a SELECT statement that has functions in it."""
        s = select([users, (users.c.user_id * 2).label('concat'), func.count(addresses.c.address_id).label('count')],
        users.c.user_id==addresses.c.user_id, group_by=[c for c in users.c]).alias('myselect')
        mapper(User, s)
        sess = create_session()
        l = sess.query(User).select()
        for u in l:
            print "User", u.user_id, u.user_name, u.concat, u.count
        assert l[0].concat == l[0].user_id * 2 == 14
        assert l[1].concat == l[1].user_id * 2 == 16
        
    @testbase.unsupported('firebird') 
    def testcount(self):
        """test the count function on Query
        
        (why doesnt this work on firebird?)"""
        mapper(User, users)
        q = create_session().query(User)
        self.assert_(q.count()==3)
        self.assert_(q.count(users.c.user_id.in_(8,9))==2)
        self.assert_(q.count_by(user_name='fred')==1)
            

    def testoverride(self):
        # assert that overriding a column raises an error
        try:
            m = mapper(User, users, properties = {
                    'user_name' : relation(mapper(Address, addresses)),
                }).compile()
            self.assert_(False, "should have raised ArgumentError")
        except exceptions.ArgumentError, e:
            self.assert_(True)
        
        clear_mappers()
        # assert that allow_column_override cancels the error
        m = mapper(User, users, properties = {
                'user_name' : relation(mapper(Address, addresses))
            }, allow_column_override=True)
            
        clear_mappers()
        # assert that the column being named else where also cancels the error
        m = mapper(User, users, properties = {
                'user_name' : relation(mapper(Address, addresses)),
                'foo' : users.c.user_name,
            })

    def testsynonym(self):
        sess = create_session()
        mapper(User, users, properties = dict(
            addresses = relation(mapper(Address, addresses), lazy = True),
            uname = synonym('user_name', proxy=True),
            adlist = synonym('addresses', proxy=True)
        ))
        
        u = sess.query(User).get_by(uname='jack')
        self.assert_result(u.adlist, Address, *(user_address_result[0]['addresses'][1]))

        assert u not in sess.dirty
        u.uname = "some user name"
        assert u.uname == "some user name"
        assert u.user_name == "some user name"
        assert u in sess.dirty

    def testsynonymoptions(self):
        sess = create_session()
        mapper(User, users, properties = dict(
            addresses = relation(mapper(Address, addresses), lazy = True),
            adlist = synonym('addresses', proxy=True)
        ))
        
        def go():
            u = sess.query(User).options(eagerload('adlist')).get_by(user_name='jack')
            self.assert_result(u.adlist, Address, *(user_address_result[0]['addresses'][1]))
        self.assert_sql_count(db, go, 1)
        
    def testextensionoptions(self):
        sess  = create_session()
        class ext1(MapperExtension):
            def populate_instance(self, mapper, selectcontext, row, instance, identitykey, isnew):
                """test options at the Mapper._instance level"""
                instance.TEST = "hello world"
                return EXT_PASS
        mapper(User, users, extension=ext1(), properties={
            'addresses':relation(mapper(Address, addresses), lazy=False)
        })
        class testext(MapperExtension):
            def select_by(self, *args, **kwargs):
                """test options at the Query level"""
                return "HI"
            def populate_instance(self, mapper, selectcontext, row, instance, identitykey, isnew):
                """test options at the Mapper._instance level"""
                instance.TEST_2 = "also hello world"
                return EXT_PASS
        l = sess.query(User).options(extension(testext())).select_by(x=5)
        assert l == "HI"
        l = sess.query(User).options(extension(testext())).get(7)
        assert l.user_id == 7
        assert l.TEST == "hello world"
        assert l.TEST_2 == "also hello world"
        assert not hasattr(l.addresses[0], 'TEST')
        assert not hasattr(l.addresses[0], 'TEST2')
        
        
        
        
    def testeageroptions(self):
        """tests that a lazy relation can be upgraded to an eager relation via the options method"""
        sess = create_session()
        mapper(User, users, properties = dict(
            addresses = relation(mapper(Address, addresses), lazy = True)
        ))
        l = sess.query(User).options(eagerload('addresses')).select()

        def go():
            self.assert_result(l, User, *user_address_result)
        self.assert_sql_count(db, go, 0)

    def testeageroptionswithlimit(self):
        sess = create_session()
        mapper(User, users, properties = dict(
            addresses = relation(mapper(Address, addresses), lazy = True)
        ))
        u = sess.query(User).options(eagerload('addresses')).get_by(user_id=8)

        def go():
            assert u.user_id == 8
            assert len(u.addresses) == 3
        self.assert_sql_count(db, go, 0)

    def testlazyoptionswithlimit(self):
        sess = create_session()
        mapper(User, users, properties = dict(
            addresses = relation(mapper(Address, addresses), lazy = False)
        ))
        u = sess.query(User).options(lazyload('addresses')).get_by(user_id=8)

        def go():
            assert u.user_id == 8
            assert len(u.addresses) == 3
        self.assert_sql_count(db, go, 1)

    def testeagerdegrade(self):
        """tests that an eager relation automatically degrades to a lazy relation if eager columns are not available"""
        sess = create_session()
        usermapper = mapper(User, users, properties = dict(
            addresses = relation(mapper(Address, addresses), lazy = False)
        )).compile()

        # first test straight eager load, 1 statement
        def go():
            l = sess.query(usermapper).select()
            self.assert_result(l, User, *user_address_result)
        self.assert_sql_count(db, go, 1)

        sess.clear()
        
        # then select just from users.  run it into instances.
        # then assert the data, which will launch 3 more lazy loads
        # (previous users in session fell out of scope and were removed from session's identity map)
        def go():
            r = users.select().execute()
            l = usermapper.instances(r, sess)
            self.assert_result(l, User, *user_address_result)
        self.assert_sql_count(db, go, 4)
        
        clear_mappers()

        sess.clear()
        
        # test with a deeper set of eager loads.  when we first load the three
        # users, they will have no addresses or orders.  the number of lazy loads when
        # traversing the whole thing will be three for the addresses and three for the 
        # orders.
        # (previous users in session fell out of scope and were removed from session's identity map)
        usermapper = mapper(User, users,
            properties = {
                'addresses':relation(mapper(Address, addresses), lazy=False),
                'orders': relation(mapper(Order, orders, properties = {
                    'items' : relation(mapper(Item, orderitems, properties = {
                        'keywords' : relation(mapper(Keyword, keywords), itemkeywords, lazy=False)
                    }), lazy=False)
                }), lazy=False)
            })

        sess.clear()

        # first test straight eager load, 1 statement
        def go():
            l = sess.query(usermapper).select()
            self.assert_result(l, User, *user_all_result)
        self.assert_sql_count(db, go, 1)

        sess.clear()
        
        # then select just from users.  run it into instances.
        # then assert the data, which will launch 6 more lazy loads
        def go():
            r = users.select().execute()
            l = usermapper.instances(r, sess)
            self.assert_result(l, User, *user_all_result)
        self.assert_sql_count(db, go, 7)
        
        
    def testlazyoptions(self):
        """tests that an eager relation can be upgraded to a lazy relation via the options method"""
        sess = create_session()
        mapper(User, users, properties = dict(
            addresses = relation(mapper(Address, addresses), lazy = False)
        ))
        l = sess.query(User).options(lazyload('addresses')).select()
        def go():
            self.assert_result(l, User, *user_address_result)
        self.assert_sql_count(db, go, 3)

    def testlatecompile(self):
        """tests mappers compiling late in the game"""
        
        mapper(User, users, properties = {'orders': relation(Order)})
        mapper(Item, orderitems, properties={'keywords':relation(Keyword, secondary=itemkeywords)})
        mapper(Keyword, keywords)
        mapper(Order, orders, properties={'items':relation(Item)})
        
        sess = create_session()
        u = sess.query(User).select()
        def go():
            print u[0].orders[1].items[0].keywords[1]
        self.assert_sql_count(db, go, 3)

    def testdeepoptions(self):
        mapper(User, users,
            properties = {
                'orders': relation(mapper(Order, orders, properties = {
                    'items' : relation(mapper(Item, orderitems, properties = {
                        'keywords' : relation(mapper(Keyword, keywords), itemkeywords)
                    }))
                }))
            })
            
        sess = create_session()
        
        # eagerload nothing.
        u = sess.query(User).select()
        def go():
            print u[0].orders[1].items[0].keywords[1]
        self.assert_sql_count(db, go, 3)
        sess.clear()
        
        
        print "-------MARK----------"
        # eagerload orders, orders.items, orders.items.keywords
        q2 = sess.query(User).options(eagerload('orders'), eagerload('orders.items'), eagerload('orders.items.keywords'))
        u = q2.select()
        print "-------MARK2----------"
        self.assert_sql_count(db, go, 0)

        sess.clear()
        
        # eagerload "keywords" on items.  it will lazy load "orders", then lazy load
        # the "items" on the order, but on "items" it will eager load the "keywords"
        print "-------MARK3----------"
        q3 = sess.query(User).options(eagerload('orders.items.keywords'))
        u = q3.select()
        self.assert_sql_count(db, go, 2)
        
class InheritanceTest(MapperSuperTest):

    def testinherits(self):
        class _Order(object):
            pass
        ordermapper = mapper(_Order, orders)
            
        class _User(object):
            pass
        usermapper = mapper(_User, users, properties = dict(
                orders = relation(ordermapper, lazy = False)
            ))

        class AddressUser(_User):
            pass
        mapper(AddressUser, addresses, inherits = usermapper)
        
        sess = create_session()
        q = sess.query(AddressUser)    
        l = q.select()
        
        jack = l[0]
        self.assert_(jack.user_name=='jack')
        jack.email_address = 'jack@gmail.com'
        sess.flush()
        sess.clear()
        au = q.get_by(user_name='jack')
        self.assert_(au.email_address == 'jack@gmail.com')

    def testinherits2(self):
        class _Order(object):
            pass
        class _Address(object):
            pass
        class AddressUser(_Address):
            pass
        ordermapper = mapper(_Order, orders)
        addressmapper = mapper(_Address, addresses)
        usermapper = mapper(AddressUser, users, inherits = addressmapper,
            properties = {
                'orders' : relation(ordermapper, lazy=False)
            })
        sess = create_session()
        l = sess.query(usermapper).select()
        jack = l[0]
        self.assert_(jack.user_name=='jack')
        jack.email_address = 'jack@gmail.com'
        sess.flush()
        sess.clear()
        au = sess.query(usermapper).get_by(user_name='jack')
        self.assert_(au.email_address == 'jack@gmail.com')

    def testlazyoption(self):
        """test that a lazy options gets created against its correct mapper when
        using options with inheriting mappers"""
        class _Order(object):
            pass
        class _User(object):
            pass
        class AddressUser(_User):
            pass
        ordermapper = mapper(_Order, orders)
        usermapper = mapper(_User, users, 
            properties = {
                'orders' : relation(ordermapper, lazy=True)
            })
        amapper = mapper(AddressUser, addresses, inherits = usermapper)
            
        sess = create_session()

        def go():
            l = sess.query(AddressUser).options(lazyload('orders')).select()
            # this would fail because the "orders" lazyloader gets created against AddressUsers selectable
            # and not _User's.
            assert len(l[0].orders) == 3
        self.assert_sql_count(db, go, 2)
        
            
    
class DeferredTest(MapperSuperTest):

    def testbasic(self):
        """tests a basic "deferred" load"""
        
        m = mapper(Order, orders, properties={
            'description':deferred(orders.c.description)
        })
        
        o = Order()
        self.assert_(o.description is None)

        q = create_session().query(m)
        def go():
            l = q.select()
            o2 = l[2]
            print o2.description

        orderby = str(orders.default_order_by()[0].compile(engine=db))
        self.assert_sql(db, go, [
            ("SELECT orders.order_id AS orders_order_id, orders.user_id AS orders_user_id, orders.isopen AS orders_isopen FROM orders ORDER BY %s" % orderby, {}),
            ("SELECT orders.description AS orders_description FROM orders WHERE orders.order_id = :orders_order_id", {'orders_order_id':3})
        ])

    def testunsaved(self):
        """test that deferred loading doesnt kick in when just PK cols are set"""
        m = mapper(Order, orders, properties={
            'description':deferred(orders.c.description)
        })
        
        sess = create_session()
        o = Order()
        sess.save(o)
        o.order_id = 7
        def go():
            o.description = "some description"
        self.assert_sql_count(testbase.db, go, 0)

    def testunsavedgroup(self):
        """test that deferred loading doesnt kick in when just PK cols are set"""
        m = mapper(Order, orders, properties={
            'description':deferred(orders.c.description, group='primary'),
            'opened':deferred(orders.c.isopen, group='primary')
        })

        sess = create_session()
        o = Order()
        sess.save(o)
        o.order_id = 7
        def go():
            o.description = "some description"
        self.assert_sql_count(testbase.db, go, 0)
        
    def testsave(self):
        m = mapper(Order, orders, properties={
            'description':deferred(orders.c.description)
        })
        
        sess = create_session()
        q = sess.query(m)
        l = q.select()
        o2 = l[2]
        o2.isopen = 1
        sess.flush()
        
    def testgroup(self):
        """tests deferred load with a group"""
        m = mapper(Order, orders, properties = {
            'userident':deferred(orders.c.user_id, group='primary'),
            'description':deferred(orders.c.description, group='primary'),
            'opened':deferred(orders.c.isopen, group='primary')
        })
        q = create_session().query(m)
        def go():
            l = q.select()
            o2 = l[2]
            print o2.opened, o2.description, o2.userident
            assert o2.opened == 1
            assert o2.userident == 7
            assert o2.description == 'order 3'
        orderby = str(orders.default_order_by()[0].compile(db))
        self.assert_sql(db, go, [
            ("SELECT orders.order_id AS orders_order_id FROM orders ORDER BY %s" % orderby, {}),
            ("SELECT orders.user_id AS orders_user_id, orders.description AS orders_description, orders.isopen AS orders_isopen FROM orders WHERE orders.order_id = :orders_order_id", {'orders_order_id':3})
        ])
        
    def testoptions(self):
        """tests using options on a mapper to create deferred and undeferred columns"""
        m = mapper(Order, orders)
        sess = create_session()
        q = sess.query(m)
        q2 = q.options(defer('user_id'))
        def go():
            l = q2.select()
            print l[2].user_id
            
        orderby = str(orders.default_order_by()[0].compile(db))
        self.assert_sql(db, go, [
            ("SELECT orders.order_id AS orders_order_id, orders.description AS orders_description, orders.isopen AS orders_isopen FROM orders ORDER BY %s" % orderby, {}),
            ("SELECT orders.user_id AS orders_user_id FROM orders WHERE orders.order_id = :orders_order_id", {'orders_order_id':3})
        ])
        sess.clear()
        q3 = q2.options(undefer('user_id'))
        def go():
            l = q3.select()
            print l[3].user_id
        self.assert_sql(db, go, [
            ("SELECT orders.order_id AS orders_order_id, orders.user_id AS orders_user_id, orders.description AS orders_description, orders.isopen AS orders_isopen FROM orders ORDER BY %s" % orderby, {}),
        ])

        
    def testdeepoptions(self):
        m = mapper(User, users, properties={
            'orders':relation(mapper(Order, orders, properties={
                'items':relation(mapper(Item, orderitems, properties={
                    'item_name':deferred(orderitems.c.item_name)
                }))
            }))
        })
        sess = create_session()
        q = sess.query(m)
        l = q.select()
        item = l[0].orders[1].items[1]
        def go():
            print item.item_name
        self.assert_sql_count(db, go, 1)
        self.assert_(item.item_name == 'item 4')
        sess.clear()
        q2 = q.options(undefer('orders.items.item_name'))
        l = q2.select()
        item = l[0].orders[1].items[1]
        def go():
            print item.item_name
        self.assert_sql_count(db, go, 0)
        self.assert_(item.item_name == 'item 4')
    

class NoLoadTest(MapperSuperTest):
    def testbasic(self):
        """tests a basic one-to-many lazy load"""
        m = mapper(User, users, properties = dict(
            addresses = relation(mapper(Address, addresses), lazy=None)
        ))
        q = create_session().query(m)
        l = [None]
        def go():
            x = q.select(users.c.user_id == 7)
            x[0].addresses
            l[0] = x
        self.assert_sql_count(testbase.db, go, 1)
            
        self.assert_result(l[0], User,
            {'user_id' : 7, 'addresses' : (Address, [])},
            )
    def testoptions(self):
        m = mapper(User, users, properties = dict(
            addresses = relation(mapper(Address, addresses), lazy=None)
        ))
        q = create_session().query(m).options(lazyload('addresses'))
        l = [None]
        def go():
            x = q.select(users.c.user_id == 7)
            x[0].addresses
            l[0] = x
        self.assert_sql_count(testbase.db, go, 2)
            
        self.assert_result(l[0], User,
            {'user_id' : 7, 'addresses' : (Address, [{'address_id' : 1}])},
            )


class LazyTest(MapperSuperTest):

    def testbasic(self):
        """tests a basic one-to-many lazy load"""
        m = mapper(User, users, properties = dict(
            addresses = relation(mapper(Address, addresses), lazy = True)
        ))
        q = create_session().query(m)
        l = q.select(users.c.user_id == 7)
        self.assert_result(l, User,
            {'user_id' : 7, 'addresses' : (Address, [{'address_id' : 1}])},
            )

    def testbindstosession(self):
        ctx = SessionContext(create_session)
        m = mapper(User, users, properties = dict(
            addresses = relation(mapper(Address, addresses, extension=ctx.mapper_extension), lazy=True)
        ), extension=ctx.mapper_extension)
        q = ctx.current.query(m)
        u = q.filter(users.c.user_id == 7).selectfirst()
        ctx.current.expunge(u)
        self.assert_result([u], User,
            {'user_id' : 7, 'addresses' : (Address, [{'address_id' : 1}])},
            )
    
    def testcreateinstance(self):
        class Ext(MapperExtension):
            def create_instance(self, *args, **kwargs):
                return User()
        m = mapper(Address, addresses)
        m = mapper(User, users, extension=Ext(), properties = dict(
            addresses = relation(Address, lazy=True),
        ))
        
        q = create_session().query(m)
        l = q.select();
        self.assert_result(l, User, *user_address_result)
        
    def testorderby(self):
        m = mapper(Address, addresses)

        m = mapper(User, users, properties = dict(
            addresses = relation(m, lazy = True, order_by=addresses.c.email_address),
        ))
        q = create_session().query(m)
        l = q.select()

        self.assert_result(l, User,
            {'user_id' : 7, 'addresses' : (Address, [{'email_address' : 'jack@bean.com'}])},
            {'user_id' : 8, 'addresses' : (Address, [{'email_address':'ed@bettyboop.com'}, {'email_address':'ed@lala.com'}, {'email_address':'ed@wood.com'}])},
            {'user_id' : 9, 'addresses' : (Address, [])}
            )

    def testorderby_select(self):
        """tests that a regular mapper select on a single table can order by a relation to a second table"""
        m = mapper(Address, addresses)

        m = mapper(User, users, properties = dict(
            addresses = relation(m, lazy = True),
        ))
        q = create_session().query(m)
        l = q.select(users.c.user_id==addresses.c.user_id, order_by=addresses.c.email_address)

        self.assert_result(l, User,
            {'user_id' : 8, 'addresses' : (Address, [{'email_address':'ed@wood.com'}, {'email_address':'ed@bettyboop.com'}, {'email_address':'ed@lala.com'}, ])},
            {'user_id' : 7, 'addresses' : (Address, [{'email_address' : 'jack@bean.com'}])},
        )
        
    def testorderby_desc(self):
        m = mapper(Address, addresses)

        m = mapper(User, users, properties = dict(
            addresses = relation(m, lazy = True, order_by=[desc(addresses.c.email_address)]),
        ))
        q = create_session().query(m)
        l = q.select()

        self.assert_result(l, User,
            {'user_id' : 7, 'addresses' : (Address, [{'email_address' : 'jack@bean.com'}])},
            {'user_id' : 8, 'addresses' : (Address, [{'email_address':'ed@wood.com'}, {'email_address':'ed@lala.com'}, {'email_address':'ed@bettyboop.com'}])},
            {'user_id' : 9, 'addresses' : (Address, [])},
            )

    def testorphanstate(self):
        """test that a lazily loaded child object is not marked as an orphan"""
        m = mapper(User, users, properties={
            'addresses':relation(Address, cascade="all,delete-orphan", lazy=True)
        })
        mapper(Address, addresses)

        q = create_session().query(m)
        user = q.get(7)
        assert getattr(User, 'addresses').hasparent(user.addresses[0], optimistic=True)
        assert not class_mapper(Address)._is_orphan(user.addresses[0])
        
    def testlimit(self):
        ordermapper = mapper(Order, orders, properties = dict(
                items = relation(mapper(Item, orderitems), lazy = True)
            ))

        m = mapper(User, users, properties = dict(
            addresses = relation(mapper(Address, addresses), lazy = True),
            orders = relation(ordermapper, primaryjoin = users.c.user_id==orders.c.user_id, lazy = True),
        ))
        sess= create_session()
        q = sess.query(m)
        
        if db.engine.name == 'mssql':
            l = q.select(limit=2)
            self.assert_result(l, User, *user_all_result[:2])
        else:        
            l = q.select(limit=2, offset=1)
            self.assert_result(l, User, *user_all_result[1:3])

        # use a union all to get a lot of rows to join against
        u2 = users.alias('u2')
        s = union_all(u2.select(use_labels=True), u2.select(use_labels=True), u2.select(use_labels=True)).alias('u')
        print [key for key in s.c.keys()]
        l = q.select(s.c.u2_user_id==User.c.user_id, distinct=True)
        self.assert_result(l, User, *user_all_result)
        
        sess.clear()
        clear_mappers()
        m = mapper(Item, orderitems, properties = dict(
                keywords = relation(mapper(Keyword, keywords), itemkeywords, lazy = True),
            ))
        
        l = sess.query(m).select((Item.c.item_name=='item 2') | (Item.c.item_name=='item 5') | (Item.c.item_name=='item 3'), order_by=[Item.c.item_id], limit=2)        
        self.assert_result(l, Item, *[item_keyword_result[1], item_keyword_result[2]])

    def testonetoone(self):
        m = mapper(User, users, properties = dict(
            address = relation(mapper(Address, addresses), lazy = True, uselist = False)
        ))
        q = create_session().query(m)
        l = q.select(users.c.user_id == 7)
        self.assert_result(l, User, {'user_id':7, 'address' : (Address, {'address_id':1})})

    def testbackwardsonetoone(self):
        m = mapper(Address, addresses, properties = dict(
            user = relation(mapper(User, users), lazy = True)
        ))
        q = create_session().query(m)
        l = q.select(addresses.c.address_id == 1)
        self.echo(repr(l))
        print repr(l[0].__dict__)
        self.echo(repr(l[0].user))
        self.assert_(l[0].user is not None)


    def testdouble(self):
        """tests lazy loading with two relations simulatneously, from the same table, using aliases.  """
        openorders = alias(orders, 'openorders')
        closedorders = alias(orders, 'closedorders')
        m = mapper(User, users, properties = dict(
            addresses = relation(mapper(Address, addresses), lazy = True),
            open_orders = relation(mapper(Order, openorders, entity_name='open'), primaryjoin = and_(openorders.c.isopen == 1, users.c.user_id==openorders.c.user_id), lazy = True),
            closed_orders = relation(mapper(Order, closedorders,entity_name='closed'), primaryjoin = and_(closedorders.c.isopen == 0, users.c.user_id==closedorders.c.user_id), lazy = True)
        ))
        q = create_session().query(m)
        l = q.select()
        self.assert_result(l, User,
            {'user_id' : 7, 
                'addresses' : (Address, [{'address_id' : 1}]),
                'open_orders' : (Order, [{'order_id' : 3}]),
                'closed_orders' : (Order, [{'order_id' : 1},{'order_id' : 5},])
            },
            {'user_id' : 8, 
                'addresses' : (Address, [{'address_id' : 2}, {'address_id' : 3}, {'address_id' : 4}]),
                'open_orders' : (Order, []),
                'closed_orders' : (Order, [])
            },
            {'user_id' : 9, 
                'addresses' : (Address, []),
                'open_orders' : (Order, [{'order_id' : 4}]),
                'closed_orders' : (Order, [{'order_id' : 2}])
            }
            )

    def testmanytomany(self):
        """tests a many-to-many lazy load"""
        mapper(Item, orderitems, properties = dict(
                keywords = relation(mapper(Keyword, keywords), itemkeywords, lazy = True),
            ))
        q = create_session().query(Item)
        l = q.select()
        self.assert_result(l, Item, 
            {'item_id' : 1, 'keywords' : (Keyword, [{'keyword_id' : 2}, {'keyword_id' : 4}, {'keyword_id' : 6}])},
            {'item_id' : 2, 'keywords' : (Keyword, [{'keyword_id' : 2}, {'keyword_id' : 5}, {'keyword_id' : 7}])},
            {'item_id' : 3, 'keywords' : (Keyword, [{'keyword_id' : 3}, {'keyword_id' : 4}, {'keyword_id' : 6}])},
            {'item_id' : 4, 'keywords' : (Keyword, [])},
            {'item_id' : 5, 'keywords' : (Keyword, [])}
        )
        l = q.select(and_(keywords.c.name == 'red', keywords.c.keyword_id == itemkeywords.c.keyword_id, Item.c.item_id==itemkeywords.c.item_id))
        self.assert_result(l, Item, 
            {'item_id' : 1, 'keywords' : (Keyword, [{'keyword_id' : 2}, {'keyword_id' : 4}, {'keyword_id' : 6}])},
            {'item_id' : 2, 'keywords' : (Keyword, [{'keyword_id' : 2}, {'keyword_id' : 5}, {'keyword_id' : 7}])},
        )

class EagerTest(MapperSuperTest):
    def testbasic(self):
        """tests a basic one-to-many eager load"""
        m = mapper(Address, addresses)
        
        m = mapper(User, users, properties = dict(
            addresses = relation(m, lazy = False),
        ))
        q = create_session().query(m)
        l = q.select()
        self.assert_result(l, User, *user_address_result)
        
    def testorderby(self):
        m = mapper(Address, addresses)
        
        m = mapper(User, users, properties = dict(
            addresses = relation(m, lazy = False, order_by=addresses.c.email_address),
        ))
        q = create_session().query(m)
        l = q.select()
        self.assert_result(l, User,
            {'user_id' : 7, 'addresses' : (Address, [{'email_address' : 'jack@bean.com'}])},
            {'user_id' : 8, 'addresses' : (Address, [{'email_address':'ed@bettyboop.com'}, {'email_address':'ed@lala.com'}, {'email_address':'ed@wood.com'}])},
            {'user_id' : 9, 'addresses' : (Address, [])}
            )

        
    def testorderby_desc(self):
        m = mapper(Address, addresses)

        m = mapper(User, users, properties = dict(
            addresses = relation(m, lazy = False, order_by=[desc(addresses.c.email_address)]),
        ))
        q = create_session().query(m)
        l = q.select()

        self.assert_result(l, User,
            {'user_id' : 7, 'addresses' : (Address, [{'email_address' : 'jack@bean.com'}])},
            {'user_id' : 8, 'addresses' : (Address, [{'email_address':'ed@wood.com'},{'email_address':'ed@lala.com'},  {'email_address':'ed@bettyboop.com'}, ])},
            {'user_id' : 9, 'addresses' : (Address, [])},
            )
    
    def testlimit(self):
        ordermapper = mapper(Order, orders, properties = dict(
                items = relation(mapper(Item, orderitems), lazy = False)
            ))

        m = mapper(User, users, properties = dict(
            addresses = relation(mapper(Address, addresses), lazy = False),
            orders = relation(ordermapper, primaryjoin = users.c.user_id==orders.c.user_id, lazy = False),
        ))
        sess = create_session()
        q = sess.query(m)
        
        if db.engine.name == 'mssql':
            l = q.select(limit=2)
            self.assert_result(l, User, *user_all_result[:2])
        else:        
            l = q.select(limit=2, offset=1)
            self.assert_result(l, User, *user_all_result[1:3])

        # this is an involved 3x union of the users table to get a lot of rows.
        # then see if the "distinct" works its way out.  you actually get the same
        # result with or without the distinct, just via less or more rows.
        u2 = users.alias('u2')
        s = union_all(u2.select(use_labels=True), u2.select(use_labels=True), u2.select(use_labels=True)).alias('u')
        l = q.select(s.c.u2_user_id==User.c.user_id, distinct=True)
        self.assert_result(l, User, *user_all_result)
        sess.clear()
        clear_mappers()
        m = mapper(Item, orderitems, properties = dict(
                keywords = relation(mapper(Keyword, keywords), itemkeywords, lazy = False, order_by=[keywords.c.keyword_id]),
            ))
        q = sess.query(m)
        l = q.select((Item.c.item_name=='item 2') | (Item.c.item_name=='item 5') | (Item.c.item_name=='item 3'), order_by=[Item.c.item_id], limit=2)        
        self.assert_result(l, Item, *[item_keyword_result[1], item_keyword_result[2]])
        
    def testmorelimit(self):
        """test that the ORDER BY is propigated from the inner select to the outer select, when using the 
        'wrapped' select statement resulting from the combination of eager loading and limit/offset clauses."""
        ordermapper = mapper(Order, orders, properties = dict(
                items = relation(mapper(Item, orderitems), lazy = False)
            ))

        m = mapper(User, users, properties = dict(
            addresses = relation(mapper(Address, addresses), lazy = False),
            orders = relation(ordermapper, primaryjoin = users.c.user_id==orders.c.user_id, lazy = False),
        ))
        sess = create_session()
        q = sess.query(m)
        
        if db.engine.name != 'mssql':
            l = q.select(q.join_to('orders'), order_by=desc(orders.c.user_id), limit=2, offset=1)
            self.assert_result(l, User, *(user_all_result[2], user_all_result[0]))
        
        l = q.select(q.join_to('addresses'), order_by=desc(addresses.c.email_address), limit=1, offset=0)
        self.assert_result(l, User, *(user_all_result[0],))
        
    def testonetoone(self):
        m = mapper(User, users, properties = dict(
            address = relation(mapper(Address, addresses), lazy = False, uselist = False)
        ))
        q = create_session().query(m)
        l = q.select(users.c.user_id == 7)
        self.assert_result(l, User,
            {'user_id' : 7, 'address' : (Address, {'address_id' : 1, 'email_address': 'jack@bean.com'})},
            )

    def testbackwardsonetoone(self):
        m = mapper(Address, addresses, properties = dict(
            user = relation(mapper(User, users), lazy = False)
        )).compile()
        self.echo(repr(m.props['user'].uselist))
        q = create_session().query(m)
        l = q.select(addresses.c.address_id == 1)
        self.assert_result(l, Address, 
            {'address_id' : 1, 'email_address' : 'jack@bean.com', 
                'user' : (User, {'user_id' : 7, 'user_name' : 'jack'}) 
            },
        )

    def testorphanstate(self):
        """test that an eagerly loaded child object is not marked as an orphan"""
        m = mapper(User, users, properties={
            'addresses':relation(Address, cascade="all,delete-orphan", lazy=False)
        })
        mapper(Address, addresses)
        
        s = create_session()
        q = s.query(m)
        user = q.get(7)
        assert getattr(User, 'addresses').hasparent(user.addresses[0], optimistic=True)
        assert not class_mapper(Address)._is_orphan(user.addresses[0])
        
    def testwithrepeat(self):
        """tests a one-to-many eager load where we also query on joined criterion, where the joined
        criterion is using the same tables that are used within the eager load.  the mapper must insure that the 
        criterion doesnt interfere with the eager load criterion."""
        m = mapper(User, users, properties = dict(
            addresses = relation(mapper(Address, addresses), primaryjoin = users.c.user_id==addresses.c.user_id, lazy = False)
        ))
        q = create_session().query(m)
        l = q.select(and_(addresses.c.email_address == 'ed@lala.com', addresses.c.user_id==users.c.user_id))
        self.assert_result(l, User,
            {'user_id' : 8, 'addresses' : (Address, [{'address_id' : 2, 'email_address':'ed@wood.com'}, {'address_id':3, 'email_address':'ed@bettyboop.com'}, {'address_id':4, 'email_address':'ed@lala.com'}])},
        )
        
    def testcircular(self):
        """test that a circular eager relationship breaks the cycle with a lazy loader"""
        m = mapper(User, users, properties = dict(
            addresses = relation(mapper(Address, addresses), lazy=False, backref=backref('user', lazy=False))
        ))
        assert class_mapper(User).props['addresses'].lazy is False
        assert class_mapper(Address).props['user'].lazy is False
        session = create_session()
        l = session.query(User).select()
        self.assert_result(l, User, *user_address_result)
        
    def testcompile(self):
        """tests deferred operation of a pre-compiled mapper statement"""
        session = create_session()
        m = mapper(User, users, properties = dict(
            addresses = relation(mapper(Address, addresses), lazy = False)
        ))
        s = session.query(m).compile(and_(addresses.c.email_address == bindparam('emailad'), addresses.c.user_id==users.c.user_id))
        c = s.compile()
        self.echo("\n" + str(c) + repr(c.get_params()))
        
        l = m.instances(s.execute(emailad = 'jack@bean.com'), session)
        self.echo(repr(l))
        
    def testmulti(self):
        """tests eager loading with two relations simultaneously"""
        m = mapper(User, users, properties = dict(
            addresses = relation(mapper(Address, addresses), primaryjoin = users.c.user_id==addresses.c.user_id, lazy = False),
            orders = relation(mapper(Order, orders), lazy = False),
        ))
        q = create_session().query(m)
        l = q.select()
        self.assert_result(l, User,
            {'user_id' : 7, 
                'addresses' : (Address, [{'address_id' : 1}]),
                'orders' : (Order, [{'order_id' : 1}, {'order_id' : 3},{'order_id' : 5},])
            },
            {'user_id' : 8, 
                'addresses' : (Address, [{'address_id' : 2}, {'address_id' : 3}, {'address_id' : 4}]),
                'orders' : (Order, [])
            },
            {'user_id' : 9, 
                'addresses' : (Address, []),
                'orders' : (Order, [{'order_id' : 2},{'order_id' : 4}])
            }
            )

    def testdouble(self):
        """tests eager loading with two relations simultaneously, from the same table.  """
        openorders = alias(orders, 'openorders')
        closedorders = alias(orders, 'closedorders')
        ordermapper = mapper(Order, orders)
        m = mapper(User, users, properties = dict(
            addresses = relation(mapper(Address, addresses), lazy = False),
            open_orders = relation(mapper(Order, openorders, non_primary=True), primaryjoin = and_(openorders.c.isopen == 1, users.c.user_id==openorders.c.user_id), lazy = False),
            closed_orders = relation(mapper(Order, closedorders, non_primary=True), primaryjoin = and_(closedorders.c.isopen == 0, users.c.user_id==closedorders.c.user_id), lazy = False)
        ))
        q = create_session().query(m)
        l = q.select()
        self.assert_result(l, User,
            {'user_id' : 7, 
                'addresses' : (Address, [{'address_id' : 1}]),
                'open_orders' : (Order, [{'order_id' : 3}]),
                'closed_orders' : (Order, [{'order_id' : 1},{'order_id' : 5},])
            },
            {'user_id' : 8, 
                'addresses' : (Address, [{'address_id' : 2}, {'address_id' : 3}, {'address_id' : 4}]),
                'open_orders' : (Order, []),
                'closed_orders' : (Order, [])
            },
            {'user_id' : 9, 
                'addresses' : (Address, []),
                'open_orders' : (Order, [{'order_id' : 4}]),
                'closed_orders' : (Order, [{'order_id' : 2}])
            }
            )

    def testdoublewithscalar(self):
        """tests eager loading with two relations from the same table, with one of them joining to the parent User.  the other is the primary mapper.  doesn't re-test addresses relation."""
        max_orders_by_user = select([func.max(orders.c.order_id).label('order_id')], group_by=[orders.c.user_id]).alias('max_orders_by_user')
        max_orders = orders.select(orders.c.order_id==max_orders_by_user.c.order_id).alias('max_orders')
        m = mapper(User, users, properties={
               'orders':relation(mapper(Order, orders), backref='user', lazy=False),
               'max_order':relation(mapper(Order, max_orders, non_primary=True), lazy=False, uselist=False)
               })
        q = create_session().query(m)
        l = q.select()
        self.assert_result(l, User,
            {'user_id' : 7, 
                'orders' : (Order, [{'order_id' : 1}, {'order_id' : 3},{'order_id' : 5},]),
                'max_order' : (Order, {'order_id' : 5})
            },
            {'user_id' : 8, 
                'orders' : (Order, []),
                'max_order' : None,
            },
            {'user_id' : 9, 
                'orders' : (Order, [{'order_id' : 2},{'order_id' : 4}]),
                'max_order' : (Order, {'order_id' : 4})
            }
            )

    def testnested(self):
        """tests eager loading of a parent item with two types of child items,
        where one of those child items eager loads its own child items."""
        ordermapper = mapper(Order, orders, properties = dict(
                items = relation(mapper(Item, orderitems), lazy = False)
            ))

        m = mapper(User, users, properties = dict(
            addresses = relation(mapper(Address, addresses), lazy = False),
            orders = relation(ordermapper, primaryjoin = users.c.user_id==orders.c.user_id, lazy = False),
        ))
        q = create_session().query(m)
        l = q.select()
        self.assert_result(l, User, *user_all_result)
    
    def testmanytomany(self):
        items = orderitems
        m = mapper(Item, items, properties = dict(
                keywords = relation(mapper(Keyword, keywords), itemkeywords, lazy=False, order_by=[keywords.c.keyword_id]),
            ))
        q = create_session().query(m)
        l = q.select()
        self.assert_result(l, Item, *item_keyword_result)
        
        l = q.select(and_(keywords.c.name == 'red', keywords.c.keyword_id == itemkeywords.c.keyword_id, items.c.item_id==itemkeywords.c.item_id))
        self.assert_result(l, Item, 
            {'item_id' : 1, 'keywords' : (Keyword, [{'keyword_id' : 2}, {'keyword_id' : 4}, {'keyword_id' : 6}])},
            {'item_id' : 2, 'keywords' : (Keyword, [{'keyword_id' : 2}, {'keyword_id' : 5}, {'keyword_id' : 7}])},
        )

    
    def testmanytomanyoptions(self):
        items = orderitems
        
        m = mapper(Item, items, properties = dict(
                keywords = relation(mapper(Keyword, keywords), itemkeywords, lazy=True, order_by=[keywords.c.keyword_id]),
            ))
        q = create_session().query(m).options(eagerload('keywords'))
        def go():
            l = q.select()
            self.assert_result(l, Item, *item_keyword_result)
        self.assert_sql_count(db, go, 1)
        
        def go():
            l = q.select(and_(keywords.c.name == 'red', keywords.c.keyword_id == itemkeywords.c.keyword_id, items.c.item_id==itemkeywords.c.item_id))
            self.assert_result(l, Item, 
                {'item_id' : 1, 'keywords' : (Keyword, [{'keyword_id' : 2}, {'keyword_id' : 4}, {'keyword_id' : 6}])},
                {'item_id' : 2, 'keywords' : (Keyword, [{'keyword_id' : 2}, {'keyword_id' : 5}, {'keyword_id' : 7}])},
            )
        self.assert_sql_count(db, go, 1)
        
    def testoneandmany(self):
        """tests eager load for a parent object with a child object that 
        contains a many-to-many relationship to a third object."""
        items = orderitems

        m = mapper(Item, items, 
            properties = dict(
                keywords = relation(mapper(Keyword, keywords), itemkeywords, lazy = False, order_by=[keywords.c.keyword_id]),
            ))

        m = mapper(Order, orders, properties = dict(
                items = relation(m, lazy = False)
            ))
        q = create_session().query(m)
        l = q.select("orders.order_id in (1,2,3)")
        self.assert_result(l, Order,
            {'order_id' : 1, 'items': (Item, [])}, 
            {'order_id' : 2, 'items': (Item, [
                {'item_id':1, 'item_name':'item 1', 'keywords': (Keyword, [{'keyword_id':2, 'name':'red'}, {'keyword_id':4, 'name':'big'}, {'keyword_id' : 6, 'name':'round'}])}, 
                {'item_id':2, 'item_name':'item 2','keywords' : (Keyword, [{'keyword_id' : 2, 'name':'red'}, {'keyword_id' : 5, 'name':'small'}, {'keyword_id' : 7, 'name':'square'}])}
               ])},
            {'order_id' : 3, 'items': (Item, [
                {'item_id':3, 'item_name':'item 3', 'keywords' : (Keyword, [{'keyword_id' : 3, 'name':'green'}, {'keyword_id' : 4, 'name':'big'}, {'keyword_id' : 6, 'name':'round'}])}, 
                {'item_id':4, 'item_name':'item 4'}, 
                {'item_id':5, 'item_name':'item 5'}
               ])},
        )

class InstancesTest(MapperSuperTest):
    def testcustomfromalias(self):
        mapper(User, users, properties={
            'addresses':relation(Address, lazy=True)
        })
        mapper(Address, addresses)
        query = users.select(users.c.user_id==7).union(users.select(users.c.user_id>7)).alias('ulist').outerjoin(addresses).select(use_labels=True)
        q = create_session().query(User)
        
        def go():
            l = q.options(contains_alias('ulist'), contains_eager('addresses')).instances(query.execute())
            self.assert_result(l, User, *user_address_result)
        self.assert_sql_count(testbase.db, go, 1)
        
    def testcustomeagerquery(self):
        mapper(User, users, properties={
            # setting lazy=True - the contains_eager() option below
            # should imply eagerload()
            'addresses':relation(Address, lazy=True)
        })
        mapper(Address, addresses)
        
        selectquery = users.outerjoin(addresses).select(use_labels=True)
        q = create_session().query(User)
        
        def go():
            l = q.options(contains_eager('addresses')).instances(selectquery.execute())
            self.assert_result(l, User, *user_address_result)
        self.assert_sql_count(testbase.db, go, 1)

    def testcustomeagerwithstringalias(self):
        mapper(User, users, properties={
            'addresses':relation(Address, lazy=False)
        })
        mapper(Address, addresses)

        adalias = addresses.alias('adalias')
        selectquery = users.outerjoin(adalias).select(use_labels=True)
        q = create_session().query(User)

        def go():
            l = q.options(contains_eager('addresses', alias="adalias")).instances(selectquery.execute())
            self.assert_result(l, User, *user_address_result)
        self.assert_sql_count(testbase.db, go, 1)

    def testcustomeagerwithalias(self):
        mapper(User, users, properties={
            'addresses':relation(Address, lazy=False)
        })
        mapper(Address, addresses)

        adalias = addresses.alias('adalias')
        selectquery = users.outerjoin(adalias).select(use_labels=True)
        q = create_session().query(User)

        def go():
            l = q.options(contains_eager('addresses', alias=adalias)).instances(selectquery.execute())
            self.assert_result(l, User, *user_address_result)
        self.assert_sql_count(testbase.db, go, 1)

    def testcustomeagerwithdecorator(self):
        mapper(User, users, properties={
            'addresses':relation(Address, lazy=False)
        })
        mapper(Address, addresses)

        adalias = addresses.alias('adalias')
        selectquery = users.outerjoin(adalias).select(use_labels=True)
        def decorate(row):
            d = {}
            for c in addresses.columns:
                d[c] = row[adalias.corresponding_column(c)]
            return d
            
        q = create_session().query(User)

        def go():
            l = q.options(contains_eager('addresses', decorator=decorate)).instances(selectquery.execute())
            self.assert_result(l, User, *user_address_result)
        self.assert_sql_count(testbase.db, go, 1)
    
    def testmultiplemappers(self):
        mapper(User, users, properties={
            'addresses':relation(Address, lazy=True)
        })
        mapper(Address, addresses)

        sess = create_session()
        
        (user7, user8, user9) = sess.query(User).select()
        (address1, address2, address3, address4) = sess.query(Address).select()
        
        selectquery = users.outerjoin(addresses).select(use_labels=True)
        q = sess.query(User)
        l = q.instances(selectquery.execute(), Address)
        # note the result is a cartesian product
        assert l == [
            (user7, address1),
            (user8, address2),
            (user8, address3),
            (user8, address4),
            (user9, None)
        ]
    
    def testmultipleonquery(self):
        mapper(User, users, properties={
            'addresses':relation(Address, lazy=True)
        })
        mapper(Address, addresses)
        sess = create_session()
        (user7, user8, user9) = sess.query(User).select()
        (address1, address2, address3, address4) = sess.query(Address).select()
        q = sess.query(User)
        q = q.add_entity(Address).outerjoin('addresses')
        l = q.list()
        assert l == [
            (user7, address1),
            (user8, address2),
            (user8, address3),
            (user8, address4),
            (user9, None)
        ]

    def testcolumnonquery(self):
        mapper(User, users, properties={
            'addresses':relation(Address, lazy=True)
        })
        mapper(Address, addresses)
        
        sess = create_session()
        (user7, user8, user9) = sess.query(User).select()
        q = sess.query(User)
        q = q.group_by([c for c in users.c]).outerjoin('addresses').add_column(func.count(addresses.c.address_id).label('count'))
        l = q.list()
        assert l == [
            (user7, 1),
            (user8, 3),
            (user9, 0)
        ]
        
    def testmapperspluscolumn(self):
        mapper(User, users)
        s = select([users, func.count(addresses.c.address_id).label('count')], from_obj=[users.outerjoin(addresses)], group_by=[c for c in users.c])
        sess = create_session()
        (user7, user8, user9) = sess.query(User).select()
        q = sess.query(User)
        l = q.instances(s.execute(), "count")
        assert l == [
            (user7, 1),
            (user8, 3),
            (user9, 0)
        ]


if __name__ == "__main__":    
    testbase.main()
